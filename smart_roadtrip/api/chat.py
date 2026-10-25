"""对话接口：接收结构化行程需求，启动 MainAgent 规划。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from smart_roadtrip.api.observability import audit
from smart_roadtrip.api.ownership import get_owner_store
from smart_roadtrip.api.runtime import get_graph, register_thread, thread_exists
from smart_roadtrip.api.schemas import ChatRequest, ChatResponse, TripStateView
from smart_roadtrip.api.security import (
    get_owner,
    get_owner_limit,
    require_api_key,
)
from smart_roadtrip.orchestrator.main_agent import start_planning
from smart_roadtrip.orchestrator.request_parser import (
    RequestParseError,
    parse_request_text,
)
from smart_roadtrip.schemas.trip import TripStatus

router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=ChatResponse, summary="创建行程")
def create_plan(payload: ChatRequest, request: Request) -> ChatResponse:
    """创建一次行程规划。

    请求体提供 ``trip``（结构化 TripRequest）或 ``message``（自然语言）之一。
    自然语言优先走 LLM；未配置/调用失败时自动回退规则解析。
    """

    thread_id = payload.thread_id or uuid4().hex
    if thread_exists(thread_id):
        raise HTTPException(
            status_code=409,
            detail="该 thread_id 已存在，请使用新的 thread_id 或查询已有行程。",
        )

    owner = get_owner(request)
    limit = get_owner_limit(request)
    if limit > 0:
        since = datetime.now(timezone.utc) - timedelta(days=1)
        try:
            used = get_owner_store().count_since(owner, since)
        except Exception as exc:  # noqa: BLE001 - 配额读取失败显式报错
            raise HTTPException(
                status_code=500, detail=f"读取租户配额失败：{exc}"
            ) from exc
        if used >= limit:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"该 API key 最近 24 小时创建的行程数已达上限（{limit}）。"
                ),
            )

    trip_request = payload.trip
    if trip_request is None:
        try:
            trip_request = parse_request_text(payload.message or "")
        except RequestParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        state = start_planning(trip_request, thread_id, graph=get_graph())
    except Exception as exc:  # noqa: BLE001 - API 边界统一转成 HTTP 错误
        raise HTTPException(status_code=500, detail=f"行程规划失败：{exc}") from exc

    register_thread(thread_id)
    try:
        get_owner_store().set(thread_id, owner)
    except Exception as exc:  # noqa: BLE001 - 归属写入失败必须显式报错
        raise HTTPException(
            status_code=500, detail=f"记录线程归属失败：{exc}"
        ) from exc

    audit("thread_created", owner=owner, thread_id=thread_id)

    awaiting = state.get("status") == TripStatus.AWAITING_CONFIRMATION
    return ChatResponse(
        thread_id=thread_id,
        awaiting_confirmation=awaiting,
        message="行程已生成，等待用户确认。" if awaiting else "行程已生成。",
        trip=trip_request,
        state=TripStateView.from_state(thread_id, state),
    )
