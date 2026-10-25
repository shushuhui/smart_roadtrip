"""基于多智能体协作的旅行规划系统命令行客户端。

常用示例：
    python scripts/roadtrip_cli.py plan --message "从成都到稻城亚丁玩4天"
    python scripts/roadtrip_cli.py confirm <thread_id> --approve --ack <alert_id>
    python scripts/roadtrip_cli.py list
    python scripts/roadtrip_cli.py export <thread_id> --format pdf --output trip.pdf

全局参数（--base-url / --api-key / --json）需写在子命令之前。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smart_roadtrip.client import RoadtripAPIError, RoadtripClient  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="基于多智能体协作的旅行规划系统 CLI（Smart Roadtrip）"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("ROADTRIP_BASE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--api-key", default=os.getenv("ROADTRIP_API_KEY") or None
    )
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="创建行程")
    plan.add_argument("--message", help="自然语言需求")
    plan.add_argument("--trip-file", help="TripRequest JSON 文件")
    plan.add_argument("--thread-id")

    listing = sub.add_parser("list", help="列出当前 owner 的行程")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--offset", type=int, default=0)

    get = sub.add_parser("get", help="查询行程状态")
    get.add_argument("thread_id")

    confirm = sub.add_parser("confirm", help="确认或退回行程")
    confirm.add_argument("thread_id")
    action = confirm.add_mutually_exclusive_group(required=True)
    action.add_argument("--approve", action="store_true")
    action.add_argument("--reject", action="store_true")
    confirm.add_argument("--feedback", default="")
    confirm.add_argument(
        "--ack", action="append", default=[], help="已确认的 alert_id，可重复"
    )

    report = sub.add_parser("report", help="获取 Markdown 路书")
    report.add_argument("thread_id")

    journal = sub.add_parser("journal", help="生成游记草稿")
    journal.add_argument("thread_id")

    export = sub.add_parser("export", help="导出行程文件")
    export.add_argument("thread_id")
    export.add_argument(
        "--format", choices=["md", "html", "gpx", "pdf"], default="md"
    )
    export.add_argument("--include-journal", action="store_true")
    export.add_argument("--output", help="写入文件；不填则输出到 stdout")

    sub.add_parser("usage", help="查看配额与用量")

    audit = sub.add_parser("audit", help="查看审计事件")
    audit.add_argument("--limit", type=int, default=50)
    audit.add_argument("--offset", type=int, default=0)

    delete = sub.add_parser("delete", help="删除行程")
    delete.add_argument("thread_id")

    sub.add_parser("ready", help="readiness 检查")
    sub.add_parser("health", help="健康检查")
    return parser


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    client = RoadtripClient(args.base_url, api_key=args.api_key)

    try:
        if args.command == "plan":
            if bool(args.message) == bool(args.trip_file):
                print(
                    "plan 需要且只能提供 --message 或 --trip-file",
                    file=sys.stderr,
                )
                return 2
            trip = None
            if args.trip_file:
                trip = json.loads(
                    Path(args.trip_file).read_text(encoding="utf-8")
                )
            result = client.create_plan(
                trip=trip,
                message=args.message,
                thread_id=args.thread_id,
            )
            if args.json:
                _print_json(result)
            else:
                print(f"thread_id: {result.get('thread_id')}")
                print(
                    "awaiting_confirmation: "
                    f"{result.get('awaiting_confirmation')}"
                )
            return 0

        if args.command == "list":
            items = client.list_trips(
                limit=args.limit, offset=args.offset
            )
            if args.json:
                _print_json(items)
            else:
                for item in items:
                    print(
                        f"{item['thread_id']}  {item.get('status')}  "
                        f"{item.get('origin')} → {item.get('destination')}"
                    )
            return 0

        if args.command == "get":
            result = client.get_trip(args.thread_id)
            if args.json:
                _print_json(result)
            else:
                print(f"status: {result.get('status')}")
                print(f"round: {result.get('round')}")
                print(f"current_version: {result.get('current_version_id')}")
                pending = result.get("pending_confirmation")
                if pending:
                    print(f"pending: {pending.get('question')}")
            return 0

        if args.command == "confirm":
            result = client.confirm(
                args.thread_id,
                decision="approve" if args.approve else "reject",
                feedback=args.feedback,
                acknowledged_alert_ids=args.ack,
            )
            if args.json:
                _print_json(result)
            else:
                print(f"status: {result.get('status')}")
                print(f"revision_count: {result.get('revision_count')}")
            return 0

        if args.command == "report":
            result = client.report(args.thread_id)
            print(result.get("markdown", ""))
            return 0

        if args.command == "journal":
            result = client.journal(args.thread_id)
            print(f"> 来源：{result.get('generated_by')}")
            print(result.get("markdown", ""))
            return 0

        if args.command == "export":
            data = client.export(
                args.thread_id,
                export_format=args.format,
                include_journal=args.include_journal,
            )
            if args.output:
                Path(args.output).write_bytes(data)
                print(f"已写入 {args.output}")
            elif args.format in ("pdf", "gpx"):
                sys.stdout.buffer.write(data)
            else:
                print(data.decode("utf-8"))
            return 0

        if args.command == "usage":
            result = client.usage()
            if args.json:
                _print_json(result)
            else:
                print(f"used_24h: {result.get('used_24h')}")
                print(f"limit: {result.get('limit')}")
                print(f"remaining: {result.get('remaining')}")
                print(f"total_threads: {result.get('total_threads')}")
            return 0

        if args.command == "audit":
            items = client.audit(limit=args.limit, offset=args.offset)
            if args.json:
                _print_json(items)
            else:
                for item in items:
                    print(
                        f"{item.get('created_at')}  {item.get('event')}  "
                        f"{item.get('thread_id')}"
                    )
            return 0

        if args.command == "delete":
            client.delete(args.thread_id)
            print(f"已删除 {args.thread_id}")
            return 0

        if args.command in ("ready", "health"):
            result = client.ready() if args.command == "ready" else client.health()
            _print_json(result)
            return 0

        return 2
    except RoadtripAPIError as exc:
        print(f"请求失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一提示
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
