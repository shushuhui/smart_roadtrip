"""RouteAgent：路线规划、多方案对比、驾驶负荷评估。

优先调用高德地图；未配置密钥或调用失败时，按天数与节奏给出确定性占位估算。
"""

from __future__ import annotations

import logging

from smart_roadtrip.agents.base import AgentContext, BaseAgent, placeholder_result
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    AgentStatus,
    DrivingLoad,
    RouteOption,
    TravelPace,
    TripRequest,
)
from smart_roadtrip.tools import gateway
from smart_roadtrip.tools.amap import AmapRoute
from smart_roadtrip.tools.base import ToolError, ToolUnavailableError

logger = logging.getLogger(__name__)

_DAILY_KM = {
    TravelPace.RELAXED: 220.0,
    TravelPace.BALANCED: 300.0,
    TravelPace.INTENSIVE: 380.0,
}


def _driving_load(daily_km: float) -> DrivingLoad:
    if daily_km <= 250:
        return DrivingLoad.LOW
    if daily_km <= 350:
        return DrivingLoad.MEDIUM
    return DrivingLoad.HIGH


class RouteAgent(BaseAgent):
    name = AgentName.ROUTE

    def _run(self, ctx: AgentContext) -> AgentResult:
        request = ctx.request
        fallback_reason = "未配置高德地图"

        if gateway.amap_configured():
            fallback_reason = "高德路线查询失败"
            try:
                routes = gateway.amap_route_plan(
                    request.origin, request.destination
                )
            except ToolUnavailableError as exc:
                fallback_reason = str(exc)
            except ToolError as exc:
                logger.warning("高德路线查询失败，降级为占位数据：%s", exc)
            except Exception as exc:  # noqa: BLE001 - Agent 边界统一降级
                logger.warning(
                    "高德路线查询异常，降级为占位数据（%s）。", type(exc).__name__
                )
            else:
                if routes:
                    options = _amap_options(routes, request, ctx)
                    feedback_note = ""
                    if ctx.round > 1:
                        feedback_note = (
                            f" 已按反馈「{ctx.feedback or '压缩驾驶强度'}」重排。"
                        )
                    return AgentResult(
                        agent=AgentName.ROUTE,
                        round=ctx.round,
                        status=AgentStatus.OK,
                        summary=(
                            f"已通过高德地图生成 {len(options)} 套路线方案，"
                            f"首推「{options[0].name}」。{feedback_note}"
                        ),
                        is_placeholder=False,
                        route_options=options,
                        data_sources=["amap_driving"],
                    )

        base_km = request.days * _DAILY_KM[request.preferences.pace]
        # 后续轮次按反馈整体压缩一点里程，演示动态重排。
        base_km *= max(0.75, 1.0 - 0.04 * (ctx.round - 1))

        scenic_km = round(base_km, 1)
        efficient_km = round(base_km * 0.9, 1)
        round_tag = f"第{ctx.round}轮·" if ctx.round > 1 else ""

        options = [
            RouteOption(
                option_id=f"route_scenic_r{ctx.round}",
                name=f"{round_tag}风景优先",
                distance_km=scenic_km,
                duration_min=round(scenic_km / 60 * 60),
                tolls_cny=0.0,
                driving_load=_driving_load(scenic_km / request.days),
                highlights=_highlights(request),
                is_placeholder=True,
            ),
            RouteOption(
                option_id=f"route_fast_r{ctx.round}",
                name=f"{round_tag}效率优先",
                distance_km=efficient_km,
                duration_min=round(efficient_km / 85 * 60),
                tolls_cny=round(efficient_km * 0.45, 1),
                driving_load=_driving_load(efficient_km / request.days),
                highlights=["高速优先（占位）", "单日驾驶时长更短（占位）"],
                is_placeholder=True,
            ),
        ]

        feedback_note = ""
        if ctx.round > 1:
            feedback_note = f"已按反馈「{ctx.feedback or '压缩驾驶强度'}」调整方案。"
        summary = (
            f"{fallback_reason}，生成 {len(options)} 套占位路线方案，"
            f"首推「{options[0].name}」。{feedback_note}"
        )
        return placeholder_result(
            AgentName.ROUTE,
            summary,
            round=ctx.round,
            route_options=options,
        )


def _amap_options(
    routes: list[AmapRoute],
    request: TripRequest,
    ctx: AgentContext,
) -> list[RouteOption]:
    prefix = f"第{ctx.round}轮·" if ctx.round > 1 else ""
    options: list[RouteOption] = []
    for index, route in enumerate(routes[:2]):
        options.append(
            RouteOption(
                option_id=f"amap_r{ctx.round}_{index + 1}",
                name=f"{prefix}{route.name}",
                distance_km=route.distance_km,
                duration_min=route.duration_min,
                tolls_cny=route.tolls_cny,
                driving_load=_driving_load(route.distance_km / request.days),
                highlights=[
                    "高德地图实时路线",
                    f"{request.origin} → {request.destination}",
                ],
                polyline=route.polyline,
                is_placeholder=False,
            )
        )
    return options


def _highlights(request: TripRequest) -> list[str]:
    highlights = [f"沿途兴趣点：{item}（占位）" for item in request.preferences.interests[:2]]
    highlights.append("实时路线不可用，里程与时长均为估算")
    return highlights
