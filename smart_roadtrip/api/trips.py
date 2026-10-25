"""行程接口：状态查询、用户确认与路书导出。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from smart_roadtrip.api.audit_store import get_audit_store
from smart_roadtrip.api.observability import audit
from smart_roadtrip.api.ownership import get_owner_store
from smart_roadtrip.api.runtime import (
    delete_thread as delete_planned_thread,
    get_graph,
    thread_exists,
)
from smart_roadtrip.api.schemas import (
    AuditEvent,
    JournalResponse,
    ReportResponse,
    TripStateView,
    TripSummary,
    UsageResponse,
)
from smart_roadtrip.api.security import (
    get_owner,
    get_owner_limit,
    require_api_key,
)
from smart_roadtrip.orchestrator.export import (
    ExportError,
    render_gpx,
    render_html,
    render_markdown_export,
    render_pdf,
)
from smart_roadtrip.orchestrator.journal import JournalError, generate_travelogue
from smart_roadtrip.orchestrator.main_agent import get_planning_state, resume_planning
from smart_roadtrip.schemas.trip import ConfirmationDecision, TripStatus

def require_trip_access(thread_id: str, request: Request) -> None:
    """校验 API key、线程存在性与归属；不通过时统一返回 404/401。"""

    require_api_key(request)
    owner = get_owner(request)
    if not owner:
        raise HTTPException(status_code=401, detail="无效或缺失的 API key。")
    if not thread_exists(thread_id):
        raise HTTPException(status_code=404, detail="未找到该行程线程。")
    if not get_owner_store().check(thread_id, owner):
        raise HTTPException(status_code=404, detail="未找到该行程线程。")


router = APIRouter(prefix="/trips", tags=["trips"])


@router.get(
    "",
    summary="列出当前租户行程",
    response_model=list[TripSummary],
    dependencies=[Depends(require_api_key)],
)
def list_trips(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[TripSummary]:
    """列出当前 owner 的行程（按创建时间倒序）。"""

    owner = get_owner(request)
    if not owner:
        raise HTTPException(status_code=401, detail="无效或缺失的 API key。")

    entries = get_owner_store().list_for_owner(
        owner, limit=limit, offset=offset
    )
    graph = get_graph()
    summaries: list[TripSummary] = []
    for thread_id, created_at in entries:
        state = get_planning_state(thread_id, graph=graph)
        if not state:
            continue
        trip_request = state.get("request")
        summaries.append(
            TripSummary(
                thread_id=thread_id,
                created_at=created_at,
                status=state.get("status", TripStatus.PLANNING),
                origin=str(_value(trip_request, "origin", "") or ""),
                destination=str(
                    _value(trip_request, "destination", "") or ""
                ),
                days=int(_value(trip_request, "days", 0) or 0),
                current_version_id=state.get("current_version_id"),
                confirmed_version_id=state.get("confirmed_version_id"),
            )
        )
    return summaries


@router.get(
    "/usage",
    summary="查询租户用量与配额",
    response_model=UsageResponse,
    dependencies=[Depends(require_api_key)],
)
def usage(request: Request) -> UsageResponse:
    """返回当前 owner 最近 24 小时的行程创建用量与配额。"""

    owner = get_owner(request)
    if not owner:
        raise HTTPException(status_code=401, detail="无效或缺失的 API key。")

    limit = get_owner_limit(request)
    since = datetime.now(timezone.utc) - timedelta(days=1)
    store = get_owner_store()
    used = store.count_since(owner, since)
    remaining = max(limit - used, 0) if limit > 0 else None

    entries = store.list_for_owner(owner, limit=100, offset=0)
    graph = get_graph()
    statuses: Counter[str] = Counter()
    recent_cost = 0.0
    for thread_id, _ in entries:
        state = get_planning_state(thread_id, graph=graph)
        if not state:
            continue
        status = state.get("status", TripStatus.PLANNING)
        statuses[str(getattr(status, "value", status))] += 1
        itinerary = state.get("itinerary")
        cost = _value(itinerary, "cost_estimate") if itinerary else None
        total = _value(cost, "total_cny", 0.0) if cost else 0.0
        try:
            recent_cost += float(total or 0)
        except (TypeError, ValueError):
            continue

    return UsageResponse(
        used_24h=used,
        limit=limit,
        remaining=remaining,
        total_threads=store.count_for_owner(owner),
        recent_by_status=dict(statuses),
        recent_estimated_cost_cny=round(recent_cost, 2),
        last_created_at=entries[0][1] if entries else None,
    )


@router.get(
    "/audit",
    summary="查询租户审计事件",
    response_model=list[AuditEvent],
    dependencies=[Depends(require_api_key)],
)
def list_audit(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[AuditEvent]:
    """返回当前 owner 的审计事件（按时间倒序）。"""

    owner = get_owner(request)
    if not owner:
        raise HTTPException(status_code=401, detail="无效或缺失的 API key。")

    rows = get_audit_store().list_for_owner(
        owner, limit=limit, offset=offset
    )
    return [AuditEvent(**row) for row in rows]


@router.delete(
    "/{thread_id}",
    summary="删除行程",
    status_code=204,
    dependencies=[Depends(require_trip_access)],
)
def delete_trip(thread_id: str, request: Request) -> Response:
    """删除当前 owner 的行程归属与 checkpointer 状态。"""

    owner = get_owner(request)
    if not owner:
        raise HTTPException(status_code=401, detail="无效或缺失的 API key。")

    removed = get_owner_store().delete(thread_id, owner)
    if not removed:
        raise HTTPException(status_code=404, detail="未找到该行程线程。")

    delete_planned_thread(thread_id)
    audit("thread_deleted", owner=owner, thread_id=thread_id)
    return Response(status_code=204)


@router.get(
    "/{thread_id}",
    summary="查询行程状态",
    response_model=TripStateView,
    dependencies=[Depends(require_trip_access)],
)
def get_trip(thread_id: str) -> TripStateView:
    """查询行程当前状态、版本与待确认项。"""

    state = get_planning_state(thread_id, graph=get_graph())
    if not state:
        raise HTTPException(status_code=404, detail="未找到该行程线程。")
    return TripStateView.from_state(thread_id, state)


@router.post(
    "/{thread_id}/confirm",
    summary="确认或退回行程",
    response_model=TripStateView,
    dependencies=[Depends(require_trip_access)],
)
def confirm_trip(
    thread_id: str,
    decision: ConfirmationDecision,
    request: Request,
) -> TripStateView:
    """提交用户确认；批准后生成路书，拒绝则进入重排。"""

    graph = get_graph()
    state = get_planning_state(thread_id, graph=graph)
    if not state:
        raise HTTPException(status_code=404, detail="未找到该行程线程。")
    if state.get("status") != TripStatus.AWAITING_CONFIRMATION:
        raise HTTPException(status_code=409, detail="该行程当前不在待确认状态。")

    try:
        new_state = resume_planning(decision, thread_id, graph=graph)
    except Exception as exc:  # noqa: BLE001 - API 边界统一转成 HTTP 错误
        raise HTTPException(status_code=500, detail=f"确认失败：{exc}") from exc

    audit(
        "thread_confirmed"
        if decision.decision.value == "approve"
        else "thread_rejected",
        owner=get_owner(request),
        thread_id=thread_id,
        detail=decision.decision.value,
    )
    return TripStateView.from_state(thread_id, new_state)


@router.get(
    "/{thread_id}/report",
    summary="获取 Markdown 路书",
    response_model=ReportResponse,
    dependencies=[Depends(require_trip_access)],
)
def get_report(thread_id: str) -> ReportResponse:
    """获取已确认行程的中文 Markdown 路书。"""

    state = get_planning_state(thread_id, graph=get_graph())
    markdown = state.get("report_markdown")
    if not markdown:
        raise HTTPException(
            status_code=404,
            detail="路书尚未生成，请先确认行程。",
        )
    return ReportResponse(
        thread_id=thread_id,
        version_id=state.get("confirmed_version_id") or state.get("current_version_id"),
        markdown=markdown,
    )


@router.post(
    "/{thread_id}/journal",
    summary="生成游记草稿",
    response_model=JournalResponse,
    dependencies=[Depends(require_trip_access)],
)
def generate_journal(thread_id: str, request: Request) -> JournalResponse:
    """基于已确认行程生成中文游记草稿（LLM 可选，失败回退模板）。"""

    state = get_planning_state(thread_id, graph=get_graph())
    if not state:
        raise HTTPException(status_code=404, detail="未找到该行程线程。")
    if state.get("status") != TripStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="行程尚未确认完成，无法生成游记。",
        )

    try:
        markdown, generated_by = generate_travelogue(state)
    except JournalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - API 边界统一转成 HTTP 错误
        raise HTTPException(status_code=500, detail=f"游记生成失败：{exc}") from exc

    audit(
        "journal_generated",
        owner=get_owner(request),
        thread_id=thread_id,
        detail=generated_by,
    )
    return JournalResponse(
        thread_id=thread_id,
        version_id=state.get("confirmed_version_id")
        or state.get("current_version_id"),
        markdown=markdown,
        generated_by=generated_by,
    )


@router.get(
    "/{thread_id}/export",
    summary="导出 Markdown/HTML/GPX/PDF",
    dependencies=[Depends(require_trip_access)],
)
def export_trip(
    thread_id: str,
    request: Request,
    export_format: Literal["md", "html", "gpx", "pdf"] = Query(
        "md", alias="format"
    ),
    include_journal: bool = False,
) -> Response:
    """导出已确认行程：Markdown、独立 HTML、GPX 或 PDF。"""

    state = get_planning_state(thread_id, graph=get_graph())
    if not state:
        raise HTTPException(status_code=404, detail="未找到该行程线程。")
    if state.get("status") != TripStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="行程尚未确认完成，无法导出。",
        )

    journal: str | None = None
    if include_journal and export_format in ("md", "html", "pdf"):
        try:
            journal, _ = generate_travelogue(state)
        except Exception:  # noqa: BLE001 - 游记失败不应阻断路书导出
            journal = None

    try:
        if export_format == "md":
            content = render_markdown_export(state, journal)
            media_type = "text/markdown; charset=utf-8"
        elif export_format == "html":
            content = render_html(state, journal)
            media_type = "text/html; charset=utf-8"
        elif export_format == "pdf":
            content = render_pdf(state, journal)
            media_type = "application/pdf"
        else:
            content = render_gpx(state)
            media_type = "application/gpx+xml; charset=utf-8"
    except ExportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - API 边界统一转成 HTTP 错误
        raise HTTPException(status_code=500, detail=f"导出失败：{exc}") from exc

    safe_thread = "".join(
        char for char in thread_id if char.isalnum() or char in "-_"
    )[:60] or "trip"
    audit(
        "trip_exported",
        owner=get_owner(request),
        thread_id=thread_id,
        detail=export_format,
    )
    headers = {
        "Content-Disposition": (
            f'attachment; filename="roadtrip-{safe_thread}.{export_format}"'
        )
    }
    return Response(content=content, media_type=media_type, headers=headers)


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
