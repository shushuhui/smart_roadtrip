"""MainAgent：基于 LangGraph 的总调度。

职责：任务拆解、状态共享、动态重排行程、版本管理、失败回退、用户确认仲裁。

本阶段实现了可运行的图骨架：
- 子 Agent 为确定性占位实现，不调用外部 API / LLM；
- 用户确认使用 ``interrupt()``，需要 checkpointer 与 ``thread_id``；
- 状态变更、版本推进与回滚只发生在这一层。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt
from pydantic import BaseModel

from smart_roadtrip import config
from smart_roadtrip.agents.base import AgentContext, BaseAgent
from smart_roadtrip.agents.energy_agent import EnergyAgent
from smart_roadtrip.agents.group_agent import GroupAgent
from smart_roadtrip.agents.knowledge_agent import KnowledgeAgent
from smart_roadtrip.agents.report_agent import ReportAgent
from smart_roadtrip.agents.route_agent import RouteAgent
from smart_roadtrip.agents.safety_agent import SafetyAgent
from smart_roadtrip.agents.weather_agent import WeatherAgent
from smart_roadtrip.orchestrator.request_parser import parse_request_text
from smart_roadtrip.orchestrator.state import AgentTaskInput, TripState
from smart_roadtrip.protocols import a2a as a2a_schemas
from smart_roadtrip.protocols.a2a import (
    A2AMessage,
    A2AStatus,
    new_result,
    new_task,
)
from smart_roadtrip.schemas import trip as trip_schemas
from smart_roadtrip.schemas.trip import (
    Activity,
    AgentName,
    AgentResult,
    AgentStatus,
    AlertSeverity,
    ConfirmationDecision,
    CostEstimate,
    DayPlan,
    DecisionType,
    EnergyType,
    Itinerary,
    PendingConfirmation,
    RouteOption,
    SafetyAlert,
    TripRequest,
    TripStatus,
    TripVersion,
)

logger = logging.getLogger(__name__)

MAX_REVISIONS = 2

#: 并行扇出的 Agent；safety/report 有依赖关系，单独串行执行。
_FANOUT_AGENTS: dict[str, BaseAgent] = {
    AgentName.ROUTE.value: RouteAgent(),
    AgentName.WEATHER.value: WeatherAgent(),
    AgentName.ENERGY.value: EnergyAgent(),
    AgentName.KNOWLEDGE.value: KnowledgeAgent(),
    AgentName.GROUP.value: GroupAgent(),
}

_SAFETY_AGENT = SafetyAgent()
_REPORT_AGENT = ReportAgent()


# ---------------------------------------------------------------------------
# 需求解析与任务拆解
# ---------------------------------------------------------------------------


def parse_request(request: TripRequest | dict[str, Any] | str) -> TripRequest:
    """需求入口：结构化对象、字典或自然语言字符串。

    字符串走 ``request_parser``：配置了 LLM 时优先调用 LLM，否则/失败时
    回退到规则解析。图的下游节点不需要感知输入形式。
    """

    if isinstance(request, TripRequest):
        return request
    if isinstance(request, str):
        return parse_request_text(request)
    return TripRequest.model_validate(request)


def select_agents(request: TripRequest) -> list[str]:
    tasks = [
        AgentName.ROUTE.value,
        AgentName.WEATHER.value,
        AgentName.KNOWLEDGE.value,
        AgentName.GROUP.value,
    ]
    if request.needs_charging:
        tasks.append(AgentName.ENERGY.value)
    return tasks


# ---------------------------------------------------------------------------
# 图节点
# ---------------------------------------------------------------------------


def parse_node(state: TripState) -> dict[str, Any]:
    request = parse_request(state["request"])
    return {
        "request": request,
        "status": TripStatus.PLANNING,
        "tasks": select_agents(request),
        "round": 0,
        "revision_count": 0,
        "feedback": "",
        "agent_results": None,
        "a2a_responses": [],
        "itinerary": None,
        "pending_confirmation": None,
        "confirmation": None,
        "report_markdown": None,
        "current_version_id": None,
        "confirmed_version_id": None,
    }


def dispatch_node(state: TripState) -> dict[str, Any]:
    return {
        "tasks": select_agents(state["request"]),
        "round": state.get("round", 0) + 1,
        "agent_results": None,  # 触发 reducer 清空上一轮结果
        "status": TripStatus.PLANNING,
        "confirmation": None,
        "pending_confirmation": None,
    }


def fanout_router(state: TripState) -> list[Send]:
    request = state["request"]
    round_no = state.get("round", 1)
    revision_count = state.get("revision_count", 0)
    feedback = state.get("feedback", "")

    sends: list[Send] = []
    for task in state.get("tasks", []):
        message = new_task(
            sender=AgentName.ORCHESTRATOR,
            recipient=AgentName(task),
            task=task,
            payload={
                "round": round_no,
                "revision_count": revision_count,
                "feedback": feedback,
            },
        )
        sends.append(
            Send(
                task,
                AgentTaskInput(
                    task=task,
                    request=request,
                    round=round_no,
                    revision_count=revision_count,
                    feedback=feedback,
                    message=message,
                ),
            )
        )
    return sends


def _make_agent_node(agent: BaseAgent):
    def node(payload: AgentTaskInput) -> dict[str, Any]:
        message = payload.get("message")
        if isinstance(message, dict):
            message = A2AMessage.model_validate(message)
        ctx = AgentContext(
            request=payload["request"],
            round=payload.get("round", 1),
            revision_count=payload.get("revision_count", 0),
            feedback=payload.get("feedback", ""),
            a2a_message=message,
        )
        result = agent.run(ctx)

        update: dict[str, Any] = {
            "agent_results": {agent.name.value: result}
        }
        if message is not None:
            update["a2a_responses"] = [
                new_result(
                    message,
                    sender=agent.name,
                    status=(
                        A2AStatus.OK
                        if result.status != AgentStatus.FAILED
                        else A2AStatus.FAILED
                    ),
                    result={
                        "summary": result.summary,
                        "status": result.status.value,
                        "is_placeholder": result.is_placeholder,
                    },
                    error=result.error,
                )
            ]
        return update

    node.__name__ = f"{agent.name.value}_node"
    return node


def _get_result(state: TripState, agent: AgentName) -> AgentResult | None:
    return (state.get("agent_results") or {}).get(agent.value)


def aggregate_node(state: TripState) -> dict[str, Any]:
    results = state.get("agent_results") or {}
    errors = [
        f"{name} Agent 执行失败：{result.error or '未知错误'}"
        for name, result in results.items()
        if result.status == AgentStatus.FAILED
    ]

    route_result = results.get(AgentName.ROUTE.value)
    if (
        route_result is not None
        and route_result.status != AgentStatus.FAILED
        and route_result.route_options
    ):
        itinerary = _build_itinerary(state, route_result.route_options[0])
    else:
        itinerary = _fallback_itinerary(state)
        errors.append("路线数据不可用，已生成兜底行程；请勿作为真实路线使用。")

    return {"itinerary": itinerary, "errors": errors}


def safety_node(state: TripState) -> dict[str, Any]:
    ctx = AgentContext(
        request=state["request"],
        round=state.get("round", 1),
        revision_count=state.get("revision_count", 0),
        feedback=state.get("feedback", ""),
        itinerary=state.get("itinerary"),
        upstream=dict(state.get("agent_results") or {}),
    )
    result = _SAFETY_AGENT.run(ctx)

    if result.status == AgentStatus.FAILED:
        fallback_alert = SafetyAlert(
            severity=AlertSeverity.WARNING,
            category="安全服务",
            message="安全 Agent 未能完成检查，当前方案缺少安全评估。",
            recommendation="请务必自行核对行程风险后再决定是否采用。",
            requires_user_confirmation=True,
        )
        result = result.model_copy(
            update={
                "status": AgentStatus.PARTIAL,
                "safety_alerts": [fallback_alert],
            }
        )
        return {
            "agent_results": {AgentName.SAFETY.value: result},
            "errors": [f"safety Agent 执行失败，已使用兜底提醒：{result.error or '未知错误'}"],
        }

    return {"agent_results": {AgentName.SAFETY.value: result}}


def snapshot_version_node(state: TripState) -> dict[str, Any]:
    itinerary = state.get("itinerary") or _fallback_itinerary(state)
    versions = state.get("versions") or []
    round_no = state.get("round", 1)
    version = TripVersion(
        number=len(versions) + 1,
        reason="初始方案" if round_no <= 1 else f"第 {round_no - 1} 次修订方案",
        itinerary=itinerary,
    )
    return {
        "versions": [version],
        "current_version_id": version.version_id,
        "status": TripStatus.AWAITING_CONFIRMATION,
    }


def prepare_confirmation_node(state: TripState) -> dict[str, Any]:
    itinerary = state.get("itinerary")
    if itinerary is None:
        return {
            "status": TripStatus.FAILED,
            "errors": ["缺少行程，无法发起用户确认。"],
        }

    safety_result = _get_result(state, AgentName.SAFETY)
    alerts = safety_result.safety_alerts if safety_result else []
    versions = state.get("versions") or []
    version_number = versions[-1].number if versions else 1

    pending = PendingConfirmation(
        version_id=state.get("current_version_id") or "",
        summary_markdown=_confirmation_summary(itinerary, version_number, alerts),
        safety_alerts=alerts,
    )
    return {"pending_confirmation": pending, "status": TripStatus.AWAITING_CONFIRMATION}


def await_confirmation_node(state: TripState) -> dict[str, Any]:
    pending = state.get("pending_confirmation")
    if pending is None:
        return {
            "status": TripStatus.FAILED,
            "errors": ["缺少待确认项，无法继续。"],
        }
    if not isinstance(pending, PendingConfirmation):
        pending = PendingConfirmation.model_validate(pending)

    raw = interrupt(pending.model_dump(mode="json"))
    try:
        decision = (
            raw
            if isinstance(raw, ConfirmationDecision)
            else ConfirmationDecision.model_validate(raw)
        )
    except Exception as exc:  # noqa: BLE001 - 用户输入边界统一兜底
        decision = ConfirmationDecision(
            decision=DecisionType.REJECT,
            feedback=f"确认信息格式错误，已退回调整：{exc}",
        )

    required = {
        alert.alert_id
        for alert in pending.safety_alerts
        if alert.requires_user_confirmation
    }
    missing = required - set(decision.acknowledged_alert_ids)
    if decision.decision == DecisionType.APPROVE and missing:
        decision = decision.model_copy(
            update={
                "decision": DecisionType.REJECT,
                "feedback": "安全提示未确认，方案已退回调整。",
            }
        )

    update: dict[str, Any] = {
        "confirmation": decision,
        "pending_confirmation": None,
    }
    if decision.decision == DecisionType.APPROVE:
        update.update(
            {
                "status": TripStatus.APPROVED,
                "confirmed_version_id": pending.version_id,
            }
        )
    else:
        update.update(
            {
                "status": TripStatus.REVISING,
                "revision_count": state.get("revision_count", 0) + 1,
                "feedback": decision.feedback or "用户未提供具体调整意见。",
            }
        )
    return update


def confirmation_gate(state: TripState) -> str:
    """确认项缺失时直接进入回退，避免在错误状态上反复重排。"""

    return "await_confirmation" if state.get("pending_confirmation") else "rollback"


def make_confirmation_router(max_revisions: int):
    def route(state: TripState) -> str:
        if state.get("status") == TripStatus.FAILED:
            return "rollback"

        decision = state.get("confirmation")
        if decision is None:
            return "rollback"
        if not isinstance(decision, ConfirmationDecision):
            decision = ConfirmationDecision.model_validate(decision)
        if decision.decision == DecisionType.APPROVE:
            return "report"

        if state.get("revision_count", 0) <= max_revisions:
            return "replan"
        return "rollback"

    return route


def replan_node(state: TripState) -> dict[str, Any]:
    return {"status": TripStatus.REVISING}


def report_node(state: TripState) -> dict[str, Any]:
    ctx = AgentContext(
        request=state["request"],
        round=state.get("round", 1),
        revision_count=state.get("revision_count", 0),
        feedback=state.get("feedback", ""),
        itinerary=state.get("itinerary"),
        upstream=dict(state.get("agent_results") or {}),
    )
    result = _REPORT_AGENT.run(ctx)
    markdown = result.report_markdown or _fallback_report(state)
    update: dict[str, Any] = {
        "agent_results": {AgentName.REPORT.value: result},
        "report_markdown": markdown,
    }
    if result.status == AgentStatus.FAILED:
        update["errors"] = [
            f"report Agent 执行失败，已使用兜底路书：{result.error or '未知错误'}"
        ]
    return update


def finalize_node(state: TripState) -> dict[str, Any]:
    return {
        "status": TripStatus.COMPLETED,
        "confirmed_version_id": (
            state.get("confirmed_version_id") or state.get("current_version_id")
        ),
    }


def rollback_node(state: TripState) -> dict[str, Any]:
    confirmed_id = state.get("confirmed_version_id")
    versions = state.get("versions") or []

    if confirmed_id:
        version = next(
            (item for item in versions if item.version_id == confirmed_id), None
        )
        if version is not None:
            return {
                "itinerary": version.itinerary.model_copy(deep=True),
                "current_version_id": version.version_id,
                "status": TripStatus.ROLLED_BACK,
                "pending_confirmation": None,
                "report_markdown": None,
                "errors": [
                    f"已回退到已确认版本 v{version.number}（{version.reason}）。"
                ],
            }

    return {
        "itinerary": None,
        "current_version_id": None,
        "status": TripStatus.FAILED,
        "pending_confirmation": None,
        "report_markdown": None,
        "errors": ["无可回退的已确认版本，本次规划未生效。"],
    }


# ---------------------------------------------------------------------------
# 行程 / 路书辅助
# ---------------------------------------------------------------------------


def _estimate_cost(state: TripState, route: RouteOption) -> CostEstimate:
    """按配置参数估算能源费与过路费；不包含住宿、餐饮和门票。"""

    request = state["request"]
    distance = max(0.0, route.distance_km)
    tolls = 0.0 if route.is_placeholder else max(0.0, route.tolls_cny)
    assumptions: list[str] = []

    if route.is_placeholder:
        assumptions.append("路线为占位估算，过路费按 0 计算。")
    else:
        assumptions.append("过路费来自高德路线。")

    energy_result = _get_result(state, AgentName.ENERGY)
    energy_type = request.vehicle.energy_type
    electricity_price = config.ELECTRICITY_PRICE_CNY_PER_KWH

    if energy_type in (EnergyType.EV, EnergyType.PHEV):
        prices = [
            stop.price_per_kwh
            for stop in (energy_result.charging_stops if energy_result else [])
            if stop.price_per_kwh is not None
        ]
        if prices:
            electricity_price = sum(prices) / len(prices)
            assumptions.append(
                f"电价按补能点平均值 {electricity_price:.2f} 元/度估算。"
            )
        else:
            assumptions.append(
                f"电价使用默认值 {electricity_price:.2f} 元/度。"
            )

    fuel_cost = (
        distance
        / 100
        * config.FUEL_CONSUMPTION_L_PER_100KM
        * config.FUEL_PRICE_CNY_PER_L
    )
    electric_cost = (
        distance
        / 100
        * config.EV_CONSUMPTION_KWH_PER_100KM
        * electricity_price
    )

    if energy_type is EnergyType.EV:
        energy_cost = electric_cost
        assumptions.append(
            f"电耗按 {config.EV_CONSUMPTION_KWH_PER_100KM:.1f} kWh/100km 估算。"
        )
    elif energy_type is EnergyType.PHEV:
        energy_cost = (
            config.PHEV_FUEL_SHARE * fuel_cost
            + (1 - config.PHEV_FUEL_SHARE) * electric_cost
        )
        assumptions.append(
            f"插混按 {config.PHEV_FUEL_SHARE:.0%} 燃油 + "
            f"{1 - config.PHEV_FUEL_SHARE:.0%} 用电估算。"
        )
    else:
        energy_cost = fuel_cost
        assumptions.append(
            f"油耗按 {config.FUEL_CONSUMPTION_L_PER_100KM:.1f} L/100km、"
            f"{config.FUEL_PRICE_CNY_PER_L:.2f} 元/L 估算。"
        )

    assumptions.append("不含住宿、餐饮、门票与停车费。")
    return CostEstimate(
        energy_cost_cny=round(energy_cost, 2),
        tolls_cny=round(tolls, 2),
        total_cny=round(energy_cost + tolls, 2),
        assumptions=assumptions,
        is_placeholder=route.is_placeholder,
    )


def _build_itinerary(state: TripState, route: RouteOption) -> Itinerary:
    request = state["request"]
    round_no = state.get("round", 1)
    daily_km = round(route.distance_km / request.days, 1)
    daily_hours = round(route.duration_min / 60 / request.days, 1)

    weather_result = _get_result(state, AgentName.WEATHER)
    weather_by_day = {
        window.day_index: window
        for window in (weather_result.weather_windows if weather_result else [])
    }

    days: list[DayPlan] = []
    for day_index in range(1, request.days + 1):
        day_date = request.start_date + timedelta(days=day_index - 1)
        weather = weather_by_day.get(day_index)
        weather_note = weather.warning if weather and weather.warning else ""

        if day_index == 1:
            title = f"抵达 {request.destination}"
            activities = [
                Activity(
                    start_time="08:30",
                    title=f"从 {request.origin} 出发",
                    kind="交通",
                    duration_hours=daily_hours,
                    description="占位行程，实际路线待接入高德地图。",
                ),
                Activity(start_time="12:00", title="午餐与休息", kind="餐饮", duration_hours=1),
                Activity(
                    start_time="14:00",
                    title=f"途中游览：{request.destination}",
                    kind="游览",
                    duration_hours=2,
                ),
                Activity(start_time="18:00", title="入住与晚餐", kind="住宿", duration_hours=2),
            ]
        elif day_index == request.days:
            title = "返程与收尾"
            activities = [
                Activity(start_time="09:00", title="早餐与退房", kind="住宿", duration_hours=1),
                Activity(
                    start_time="10:00",
                    title=f"从 {request.destination} 返程",
                    kind="交通",
                    duration_hours=daily_hours,
                    description="占位行程，返程路线待接入高德地图。",
                ),
                Activity(start_time="13:00", title="午餐与休息", kind="餐饮", duration_hours=1),
            ]
        else:
            title = f"{request.destination} 深度游 · 第 {day_index} 天"
            interest = (
                request.preferences.interests[(day_index - 2) % len(request.preferences.interests)]
                if request.preferences.interests
                else "当地景点"
            )
            activities = [
                Activity(start_time="09:00", title=f"游览：{interest}", kind="游览", duration_hours=3),
                Activity(start_time="12:30", title="午餐", kind="餐饮", duration_hours=1),
                Activity(start_time="14:00", title="自由活动 / 休息", kind="休闲", duration_hours=2),
                Activity(start_time="18:00", title="晚餐与住宿", kind="住宿", duration_hours=2),
            ]

        days.append(
            DayPlan(
                day_index=day_index,
                date=day_date,
                title=title,
                activities=activities,
                driving_km=daily_km,
                notes=weather_note,
            )
        )

    real_sources: list[str] = []
    placeholder_sources: list[str] = []
    for label, agent_name in (
        ("路线", AgentName.ROUTE),
        ("天气", AgentName.WEATHER),
        ("补能", AgentName.ENERGY),
    ):
        result = _get_result(state, agent_name)
        if result is None:
            continue
        if result.is_placeholder:
            placeholder_sources.append(label)
        else:
            real_sources.append(label)

    if real_sources:
        summary = (
            f"第 {round_no} 轮方案，采用「{route.name}」。"
            f"{'、'.join(real_sources)}数据来自外部服务"
        )
        if placeholder_sources:
            summary += f"；{'、'.join(placeholder_sources)}为占位数据"

        knowledge = _get_result(state, AgentName.KNOWLEDGE)
        knowledge_real = knowledge is not None and not knowledge.is_placeholder
        summary += (
            "；救援调度尚未接入。"
            if knowledge_real
            else "；救援调度与知识库尚未接入。"
        )
        is_placeholder = False
    else:
        summary = (
            f"第 {round_no} 轮占位方案，采用「{route.name}」。"
            "路线、天气与补能数据尚未接入真实数据源。"
        )
        is_placeholder = True

    return Itinerary(
        title=f"{request.origin} → {request.destination} {request.days} 日自驾行程",
        summary=summary,
        days=days,
        total_distance_km=route.distance_km,
        total_drive_hours=round(route.duration_min / 60, 1),
        cost_estimate=_estimate_cost(state, route),
        is_placeholder=is_placeholder,
    )


def _fallback_itinerary(state: TripState) -> Itinerary:
    request = state["request"]
    days = [
        DayPlan(
            day_index=day_index,
            date=request.start_date + timedelta(days=day_index - 1),
            title=f"第 {day_index} 天（路线数据不可用）",
            activities=[
                Activity(
                    start_time="09:00",
                    title="路线规划不可用，行程待确认",
                    kind="提示",
                    description="请等待路线服务恢复后重新规划。",
                )
            ],
            notes="兜底占位行程，请勿作为真实路线使用。",
        )
        for day_index in range(1, request.days + 1)
    ]
    return Itinerary(
        title=f"{request.origin} → {request.destination}（兜底行程）",
        summary="路线 Agent 未返回可用方案，当前仅为兜底占位行程。",
        days=days,
        cost_estimate=CostEstimate(
            assumptions=["路线不可用，未估算费用。"],
            is_placeholder=True,
        ),
        is_placeholder=True,
    )


def _confirmation_summary(
    itinerary: Itinerary,
    version_number: int,
    alerts: list[SafetyAlert],
) -> str:
    lines = [
        f"## 待确认行程 v{version_number}",
        f"- 标题：{itinerary.title}",
        f"- 总里程：约 {itinerary.total_distance_km:.1f} 公里",
        f"- 总驾驶时长：约 {itinerary.total_drive_hours:.1f} 小时",
        f"- 说明：{itinerary.summary}",
    ]
    if alerts:
        lines.extend(["", "**需逐项确认的安全提示：**"])
        for alert in alerts:
            lines.append(f"- [{alert.severity.value}] {alert.message} 建议：{alert.recommendation}")
    lines.extend(
        [
            "",
            "确认即表示你已阅读并接受上述方案与安全提示；"
            "请以行程说明和路书中的数据来源标注为准。",
        ]
    )
    return "\n".join(lines)


def _fallback_report(state: TripState) -> str:
    request = state["request"]
    itinerary = state.get("itinerary")
    title = itinerary.title if itinerary else f"{request.origin} → {request.destination}"
    return (
        f"# {title}\n\n"
        "> 路书 Agent 执行失败，以下为兜底占位内容。\n\n"
        f"- 出发地：{request.origin}\n"
        f"- 目的地：{request.destination}\n"
        f"- 天数：{request.days} 天\n"
    )


# ---------------------------------------------------------------------------
# 图构建与对外入口
# ---------------------------------------------------------------------------


def _allowed_msgpack_types() -> list[tuple[str, str]]:
    """显式允许本项目的领域类型通过 checkpointer 反序列化。

    langgraph-checkpoint 新版本会对未登记类型发出警告并计划在未来阻断；
    这里按模块自动收集，新增模型无需手工维护白名单。
    """

    allowed: list[tuple[str, str]] = []
    for module in (trip_schemas, a2a_schemas):
        module_name = module.__name__
        allowed.extend(
            (module_name, name)
            for name, obj in vars(module).items()
            if isinstance(obj, type)
            and getattr(obj, "__module__", None) == module_name
            and issubclass(obj, (BaseModel, Enum))
        )
    return allowed


def _new_checkpointer(backend: str | None = None):
    """按配置创建 checkpointer。

    - memory：进程内 MemorySaver，测试默认；
    - sqlite：持久化到 ``CHECKPOINT_DB_PATH``，服务重启后仍能恢复中断行程。
    """

    serde = JsonPlusSerializer(
        allowed_msgpack_modules=_allowed_msgpack_types()
    )
    selected = (backend or config.CHECKPOINTER_BACKEND or "memory").strip().lower()

    if selected == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的明确提示
            raise RuntimeError(
                "未安装 langgraph-checkpoint-sqlite，无法使用 SQLite checkpointer。"
            ) from exc

        path = Path(config.CHECKPOINT_DB_PATH)
        if not path.is_absolute():
            path = config.PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        saver = SqliteSaver(conn, serde=serde)
        try:
            saver.setup()
        except Exception:
            conn.close()
            raise
        # 供 close_graph / reset_runtime 关闭连接；SqliteSaver 本身不提供 close。
        saver._owned_connection = conn  # type: ignore[attr-defined]
        return saver

    if selected != "memory":
        raise ValueError(
            f"未知 CHECKPOINTER_BACKEND：{selected}（可选 memory/sqlite）"
        )
    return MemorySaver(serde=serde)


def build_graph(
    *,
    checkpointer=None,
    max_revisions: int = MAX_REVISIONS,
    backend: str | None = None,
):
    """构建并编译主图。

    ``checkpointer`` 优先级最高；否则按 ``backend``（默认读
    ``config.CHECKPOINTER_BACKEND``）创建 memory 或 sqlite checkpointer。
    测试应每次新建图；生产中可以复用同一图，靠唯一 ``thread_id`` 隔离会话。
    """

    builder = StateGraph(TripState)

    builder.add_node("parse", parse_node)
    builder.add_node("dispatch", dispatch_node)
    for name, agent in _FANOUT_AGENTS.items():
        builder.add_node(name, _make_agent_node(agent))
    builder.add_node("aggregate", aggregate_node)
    builder.add_node("safety", safety_node)
    builder.add_node("snapshot_version", snapshot_version_node)
    builder.add_node("prepare_confirmation", prepare_confirmation_node)
    builder.add_node("await_confirmation", await_confirmation_node)
    builder.add_node("replan", replan_node)
    builder.add_node("rollback", rollback_node)
    builder.add_node("report", report_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "parse")
    builder.add_edge("parse", "dispatch")
    builder.add_conditional_edges(
        "dispatch", fanout_router, list(_FANOUT_AGENTS.keys())
    )
    for name in _FANOUT_AGENTS:
        builder.add_edge(name, "aggregate")
    builder.add_edge("aggregate", "safety")
    builder.add_edge("safety", "snapshot_version")
    builder.add_edge("snapshot_version", "prepare_confirmation")
    builder.add_conditional_edges(
        "prepare_confirmation",
        confirmation_gate,
        ["await_confirmation", "rollback"],
    )
    builder.add_conditional_edges(
        "await_confirmation",
        make_confirmation_router(max_revisions),
        ["report", "replan", "rollback"],
    )
    builder.add_edge("replan", "dispatch")
    builder.add_edge("report", "finalize")
    builder.add_edge("finalize", END)
    builder.add_edge("rollback", END)

    if checkpointer is None:
        checkpointer = _new_checkpointer(backend)
    return builder.compile(checkpointer=checkpointer)


def close_graph(graph) -> None:
    """关闭由 :func:`build_graph` 创建的 SQLite 连接；MemorySaver 为无操作。"""

    if graph is None:
        return
    checkpointer = getattr(graph, "checkpointer", None)
    conn = getattr(checkpointer, "_owned_connection", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - 关闭失败不应影响主流程
            logger.debug("关闭 SQLite checkpointer 失败。", exc_info=True)


_default_graph = None


def _get_default_graph():
    global _default_graph
    if _default_graph is None:
        _default_graph = build_graph()
    return _default_graph


def reset_default_graph() -> None:
    """关闭并清空模块级默认图（测试或切换 checkpointer 配置时使用）。"""

    global _default_graph
    graph = _default_graph
    _default_graph = None
    close_graph(graph)


def get_planning_state(thread_id: str, *, graph=None) -> dict[str, Any]:
    """读取某个 thread 的当前状态；未知 thread 返回空字典。"""

    graph = graph or _get_default_graph()
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    return dict(snapshot.values)


def start_planning(
    request: TripRequest | dict[str, Any] | str,
    thread_id: str,
    *,
    graph=None,
) -> dict[str, Any]:
    """启动一次规划；返回的 state 中若含 ``__interrupt__`` 即等待用户确认。

    ``request`` 可以是 ``TripRequest``、字典或自然语言字符串。字符串解析失败时
    抛出 ``RequestParseError``（含中文提示）。

    若传入自定义图，后续 :func:`resume_planning` 必须复用同一个图实例
    （checkpointer 决定中断状态能否恢复）。
    """

    graph = graph or _get_default_graph()
    parsed = parse_request(request)
    return graph.invoke(
        {"request": parsed},
        {"configurable": {"thread_id": thread_id}},
    )


def resume_planning(
    decision: ConfirmationDecision | dict[str, Any],
    thread_id: str,
    *,
    graph=None,
) -> dict[str, Any]:
    """提交用户确认并恢复同一 thread。"""

    graph = graph or _get_default_graph()
    payload = (
        decision.model_dump(mode="json")
        if isinstance(decision, ConfirmationDecision)
        else decision
    )
    return graph.invoke(
        Command(resume=payload),
        {"configurable": {"thread_id": thread_id}},
    )
