"""SafetyAgent：安全应急护航（故障/身体不适/天气路况）。

安全建议必须经用户确认，不得直接替用户决策。
当前为确定性占位实现：不调用救援/医疗 API，只基于上游占位结果生成提醒。
"""

from __future__ import annotations

import logging

from smart_roadtrip.agents.base import AgentContext, BaseAgent, placeholder_result
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    AlertSeverity,
    DrivingLoad,
    RescueResource,
    SafetyAlert,
    TripRequest,
)
from smart_roadtrip.tools import gateway
from smart_roadtrip.tools.base import ToolError

logger = logging.getLogger(__name__)


class SafetyAgent(BaseAgent):
    name = AgentName.SAFETY

    def _run(self, ctx: AgentContext) -> AgentResult:
        resources = _fetch_rescue_resources(ctx.request)
        alerts: list[SafetyAlert] = [
            SafetyAlert(
                severity=AlertSeverity.WARNING,
                category="数据完整性",
                message=_data_source_note(ctx, len(resources)),
                recommendation="出发前请自行核对实时路况、天气预警与车辆状态后再采用本方案。",
                requires_user_confirmation=True,
            )
        ]

        route = ctx.upstream.get(AgentName.ROUTE.value)
        if route and route.route_options:
            first = route.route_options[0]
            if first.driving_load is DrivingLoad.HIGH:
                alerts.append(
                    SafetyAlert(
                        severity=AlertSeverity.WARNING,
                        category="驾驶负荷",
                        message="首推方案单日驾驶里程偏高，存在疲劳驾驶风险。",
                        recommendation="建议增加休息点、缩短单日里程，或改用效率优先方案。",
                        requires_user_confirmation=True,
                    )
                )

        weather = ctx.upstream.get(AgentName.WEATHER.value)
        if weather:
            for window in weather.weather_windows:
                if window.warning:
                    alerts.append(
                        SafetyAlert(
                            severity=AlertSeverity.INFO,
                            category="天气",
                            message=f"第 {window.day_index} 天：{window.warning}",
                            recommendation="出发前复核实时天气与山区路况，必要时调整当日安排。",
                            requires_user_confirmation=True,
                        )
                    )

        if ctx.request.needs_charging and ctx.request.vehicle.current_soc < 0.35:
            alerts.append(
                SafetyAlert(
                    severity=AlertSeverity.WARNING,
                    category="补能",
                    message="出发时电量偏低，首段行程存在补能压力。",
                    recommendation="出发前充电至 80% 以上，并确认首个补能点可用。",
                    requires_user_confirmation=True,
                )
            )

        if resources:
            source_label = (
                "商业救援网络"
                if any(
                    item.source == "commercial_rescue"
                    for item in resources
                )
                else "高德 POI"
            )
            resource_note = (
                f"附近医疗/维修地点来自{source_label}"
                f"（{len(resources)} 处）。"
            )
        else:
            resource_note = "救援/医疗实时调度 API 尚未接入。"

        return placeholder_result(
            AgentName.SAFETY,
            f"生成 {len(alerts)} 条安全提醒，需用户逐项确认。{resource_note}",
            round=ctx.round,
            safety_alerts=alerts,
            rescue_resources=resources,
            data_sources=sorted(
                {item.source for item in resources}
            ),
        )


def _data_source_note(ctx: AgentContext, rescue_count: int = 0) -> str:
    real_sources: list[str] = []
    placeholder_sources: list[str] = []
    for label, agent_name in (
        ("路线", AgentName.ROUTE),
        ("天气", AgentName.WEATHER),
        ("补能", AgentName.ENERGY),
    ):
        result = ctx.upstream.get(agent_name.value)
        if result is None:
            continue
        if result.is_placeholder:
            placeholder_sources.append(label)
        else:
            real_sources.append(label)

    parts: list[str] = []
    if real_sources:
        parts.append(f"{'、'.join(real_sources)}来自外部服务")
    if placeholder_sources:
        parts.append(f"{'、'.join(placeholder_sources)}为占位数据")
    if rescue_count:
        parts.append(f"附近医疗/维修地点来自外部数据源（{rescue_count} 处）")

    if not parts:
        return "本行程的路线、天气与补能信息均为占位数据，尚未接入外部数据源。"

    note = "；".join(parts) + "。"
    if "补能" in real_sources:
        note += "补能价格/排队与实时可用性未接入；"

    knowledge = ctx.upstream.get(AgentName.KNOWLEDGE.value)
    knowledge_real = knowledge is not None and not knowledge.is_placeholder
    note += "救援调度尚未接入" if knowledge_real else "救援调度与知识库尚未接入"
    note += "。"
    return note


def _fetch_rescue_resources(request: TripRequest) -> list[RescueResource]:
    if not gateway.amap_configured():
        return []

    try:
        point = gateway.amap_geocode(request.destination)
    except ToolError as exc:
        logger.warning("救援资源地理编码失败：%s", exc)
        return []
    except Exception as exc:  # noqa: BLE001 - Agent 边界统一降级
        logger.warning(
            "救援资源地理编码异常（%s）。", type(exc).__name__
        )
        return []

    if gateway.commercial_rescue_configured():
        try:
            commercial = gateway.commercial_rescue_nearby(
                point, radius_km=50, limit=9
            )
        except ToolError as exc:
            logger.warning("商业救援网络查询失败，回退高德 POI：%s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "商业救援网络查询异常（%s）。", type(exc).__name__
            )
        else:
            if commercial:
                return commercial

    resources: list[RescueResource] = []
    searches = (
        ("医院", gateway.amap_search_hospitals),
        ("药店", gateway.amap_search_pharmacies),
        ("汽车维修", gateway.amap_search_repair_shops),
    )
    for category, search in searches:
        try:
            pois = search(point, limit=3)
        except ToolError as exc:
            logger.warning("高德 POI 查询失败（%s）：%s", category, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - Agent 边界统一降级
            logger.warning(
                "高德 POI 查询异常（%s，%s）。", category, type(exc).__name__
            )
            continue

        for poi in pois:
            resources.append(
                RescueResource(
                    name=poi.name,
                    category=category,
                    location=poi.location,
                    address=poi.address,
                    phone=poi.tel,
                    source="amap_poi",
                    notes="高德 POI；实时可用性/急诊能力未接入",
                )
            )

    unique: list[RescueResource] = []
    seen: set[tuple[str, str]] = set()
    for resource in resources:
        key = (resource.name, resource.address)
        if key in seen:
            continue
        seen.add(key)
        unique.append(resource)
    return unique[:9]
