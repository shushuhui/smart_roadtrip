"""子 Agent 公共接口与输入输出约定。

约定：
- 子 Agent 只返回结果/建议，不修改编排状态、不推进版本。
- 统一返回 :class:`AgentResult`，异常在 ``BaseAgent.run`` 中兜底为失败结果。
- 具体 Agent 不导入 orchestrator，避免反向依赖。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from smart_roadtrip.protocols.a2a import A2AMessage
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    AgentStatus,
    Itinerary,
    TripRequest,
)


@dataclass(frozen=True)
class AgentContext:
    """一次 Agent 调用所需的输入快照。

    ``upstream`` 供依赖前置结果的 Agent（如 Safety/Report）读取；
    并行扇出的 Agent 通常只使用 ``request`` 与轮次信息。
    """

    request: TripRequest
    round: int = 1
    revision_count: int = 0
    feedback: str = ""
    itinerary: Itinerary | None = None
    upstream: dict[str, AgentResult] = field(default_factory=dict)
    a2a_message: A2AMessage | None = None


def placeholder_result(
    agent: AgentName,
    summary: str,
    *,
    round: int = 1,
    **payload: Any,
) -> AgentResult:
    """构造一个显式标记为占位数据的成功结果。"""

    return AgentResult(
        agent=agent,
        round=round,
        status=AgentStatus.OK,
        summary=summary,
        is_placeholder=True,
        **payload,
    )


def failed_result(
    agent: AgentName,
    error: str,
    *,
    round: int = 1,
) -> AgentResult:
    return AgentResult(
        agent=agent,
        round=round,
        status=AgentStatus.FAILED,
        summary=f"{agent.value} Agent 执行失败。",
        error=error,
        is_placeholder=True,
    )


class BaseAgent(ABC):
    """子 Agent 模板方法：统一异常兜底与结果字段归一。"""

    name: AgentName

    def run(self, ctx: AgentContext) -> AgentResult:
        try:
            result = self._run(ctx)
        except Exception as exc:  # noqa: BLE001 - Agent 边界统一兜底
            return failed_result(self.name, f"{type(exc).__name__}: {exc}", round=ctx.round)

        if not isinstance(result, AgentResult):
            return failed_result(
                self.name,
                f"返回值类型错误：{type(result).__name__}",
                round=ctx.round,
            )

        return result.model_copy(
            update={
                "agent": self.name,
                "round": ctx.round,
            }
        )

    @abstractmethod
    def _run(self, ctx: AgentContext) -> AgentResult:
        """子类实现实际逻辑；本阶段为确定性占位实现。"""
