"""MainAgent 的 LangGraph 共享状态。

状态变更、版本推进与回滚只发生在编排层；子 Agent 只产出 ``AgentResult``。
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from smart_roadtrip.protocols.a2a import A2AMessage, A2AResponse
from smart_roadtrip.schemas.trip import (
    AgentResult,
    ConfirmationDecision,
    Itinerary,
    PendingConfirmation,
    TripRequest,
    TripStatus,
    TripVersion,
)


def merge_agent_results(
    current: dict[str, AgentResult] | None,
    update: dict[str, AgentResult] | None,
) -> dict[str, AgentResult]:
    """按 Agent 名合并结果。

    返回 ``None`` 表示新一轮开始，清空上一轮结果，避免并行分支读到旧数据。
    """

    if update is None:
        return {}
    return {**(current or {}), **update}


class AgentTaskInput(TypedDict):
    """通过 ``Send`` 扇出给单个 Agent 节点的完整输入。"""

    task: str
    request: TripRequest
    round: int
    revision_count: int
    feedback: str
    message: A2AMessage


class TripState(TypedDict, total=False):
    # 输入与控制
    request: TripRequest
    status: TripStatus
    tasks: list[str]
    round: int
    revision_count: int
    feedback: str

    # 子 Agent 结果（统一存放，避免多处副本）
    agent_results: Annotated[dict[str, AgentResult] | None, merge_agent_results]
    # A2A 结果信封历史，便于审计与后续 Agent 间协作
    a2a_responses: Annotated[list[A2AResponse], operator.add]

    # 聚合与用户确认
    itinerary: Itinerary | None
    pending_confirmation: PendingConfirmation | None
    confirmation: ConfirmationDecision | None
    report_markdown: str | None

    # 版本与回退
    versions: Annotated[list[TripVersion], operator.add]
    current_version_id: str | None
    confirmed_version_id: str | None

    # 容错
    errors: Annotated[list[str], operator.add]
