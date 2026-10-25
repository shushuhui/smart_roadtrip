"""管理员接口：跨租户用量与费用汇总。

需要配置 ``ADMIN_API_KEY``；返回的 owner 是 API key 的哈希或 ``anonymous``，
不会暴露原始 key 或 thread_id。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query

from smart_roadtrip.api.ownership import get_owner_store
from smart_roadtrip.api.runtime import get_graph
from smart_roadtrip.api.schemas import AdminUsageResponse, TenantSummary
from smart_roadtrip.api.security import require_admin_key
from smart_roadtrip.orchestrator.main_agent import get_planning_state
from smart_roadtrip.schemas.trip import TripStatus

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_key)],
)


@router.get(
    "/usage",
    response_model=AdminUsageResponse,
    summary="管理员跨租户用量汇总",
)
def admin_usage(
    limit: int = Query(100, ge=1, le=500),
) -> AdminUsageResponse:
    """按 owner 汇总线程数、最近状态分布与费用估算。

    每个 owner 最多扫描最近 100 个线程；租户数量受 ``limit`` 限制。
    """

    store = get_owner_store()
    all_owners = store.owner_summaries()
    total_threads = sum(count for _, count in all_owners)
    graph = get_graph()

    tenants: list[TenantSummary] = []
    for owner, count in all_owners[:limit]:
        entries = store.list_for_owner(owner, limit=100, offset=0)
        statuses: Counter[str] = Counter()
        estimated_cost = 0.0

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
                estimated_cost += float(total or 0)
            except (TypeError, ValueError):
                continue

        tenants.append(
            TenantSummary(
                owner=owner,
                total_threads=count,
                last_created_at=entries[0][1] if entries else None,
                by_status=dict(statuses),
                estimated_cost_cny=round(estimated_cost, 2),
            )
        )

    return AdminUsageResponse(
        total_tenants=len(all_owners),
        total_threads=total_threads,
        tenants=tenants,
        generated_at=datetime.now(timezone.utc),
    )


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
