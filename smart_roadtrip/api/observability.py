"""请求日志、基础指标与审计日志。

- 为每个响应补充 ``X-Request-ID``；
- 记录 method/path/status/耗时，路径使用路由模板，避免 thread_id 造成高基数；
- ``/metrics`` 暴露进程内累计计数，不包含密钥或请求体；
- 审计事件写到 ``smart_roadtrip.audit`` logger。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from smart_roadtrip import config

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("smart_roadtrip.audit")

class JsonFormatter(logging.Formatter):
    """把日志记录输出为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """按 ``LOG_LEVEL`` / ``LOG_FORMAT`` 配置 smart_roadtrip 包日志。"""

    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    handler = logging.StreamHandler()
    if config.LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        )

    logger = logging.getLogger("smart_roadtrip")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)


_started_at = time.time()
_metrics_lock = threading.Lock()
_counters: Counter[str] = Counter()
_duration_ms_sum = 0.0


def record_request(
    method: str,
    path: str,
    status: int,
    duration_ms: float,
) -> None:
    global _duration_ms_sum
    with _metrics_lock:
        _counters["requests_total"] += 1
        _counters[f"status_{status}"] += 1
        _counters[f"route {method} {path} {status}"] += 1
        _duration_ms_sum += duration_ms


def metrics_snapshot() -> dict:
    with _metrics_lock:
        total = _counters.get("requests_total", 0)
        by_status = {
            key.removeprefix("status_"): value
            for key, value in _counters.items()
            if key.startswith("status_")
        }
        by_route = {
            key.removeprefix("route "): value
            for key, value in _counters.items()
            if key.startswith("route ")
        }
        average = round(_duration_ms_sum / total, 2) if total else 0.0
        return {
            "uptime_seconds": round(time.time() - _started_at, 1),
            "requests_total": total,
            "by_status": by_status,
            "by_route": by_route,
            "avg_duration_ms": average,
        }


def reset_metrics() -> None:
    global _duration_ms_sum
    with _metrics_lock:
        _counters.clear()
        _duration_ms_sum = 0.0


def audit(
    event: str,
    *,
    owner: str = "anonymous",
    thread_id: str = "",
    detail: str = "",
) -> None:
    try:
        from smart_roadtrip.api.audit_store import get_audit_store

        get_audit_store().record(
            owner=owner,
            event=event,
            thread_id=thread_id,
            detail=detail,
        )
    except Exception:  # noqa: BLE001 - 审计存储失败不应阻断业务
        logger.warning("写入审计存储失败。", exc_info=True)

    audit_logger.info(
        "audit event=%s thread=%s owner=%s detail=%s",
        event,
        thread_id,
        owner,
        detail,
    )


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - 记录后继续抛出
            duration_ms = (time.perf_counter() - started) * 1000
            path = _route_template(request)
            record_request(request.method, path, 500, duration_ms)
            logger.exception(
                "request_failed method=%s path=%s duration_ms=%.1f request_id=%s",
                request.method,
                path,
                duration_ms,
                request_id,
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        path = _route_template(request)
        record_request(request.method, path, response.status_code, duration_ms)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", request.url.path))
