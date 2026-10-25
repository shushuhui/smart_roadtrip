"""游记生成编排：从已完成的行程状态生成中文游记草稿。"""

from __future__ import annotations

from typing import Any

from smart_roadtrip.agents.base import AgentContext
from smart_roadtrip.agents.report_agent import ReportAgent


class JournalError(ValueError):
    """行程状态不足以生成游记。"""


def generate_travelogue(
    state: dict[str, Any],
    *,
    agent: ReportAgent | None = None,
) -> tuple[str, str]:
    """返回 ``(markdown, generated_by)``；``generated_by`` 为 llm/template。"""

    request = state.get("request")
    itinerary = state.get("itinerary")
    if request is None or itinerary is None:
        raise JournalError("行程尚未生成，无法写游记。")

    ctx = AgentContext(
        request=request,
        round=state.get("round", 1),
        revision_count=state.get("revision_count", 0),
        feedback=state.get("feedback", ""),
        itinerary=itinerary,
        upstream=dict(state.get("agent_results") or {}),
    )
    return (agent or ReportAgent()).generate_travelogue(ctx)
