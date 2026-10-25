"""EnergyAgent：电车补能规划（续航/充电桩/排队概率）。

优先使用高德 POI 搜索目的地周边充电站；免费 POI 只有站点位置，
没有实时价格/排队/可用枪数，这些字段保持未知。未配置或查询失败时降级为占位。
"""

from __future__ import annotations

import logging

from smart_roadtrip.agents.base import AgentContext, BaseAgent, placeholder_result
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    AgentStatus,
    ChargingStop,
    EnergyType,
    TripRequest,
)
from smart_roadtrip.tools import gateway
from smart_roadtrip.tools.amap import AmapPoi
from smart_roadtrip.tools.base import ToolError, ToolUnavailableError
from smart_roadtrip.tools.charging import ChargingStation

logger = logging.getLogger(__name__)


class EnergyAgent(BaseAgent):
    name = AgentName.ENERGY

    def _run(self, ctx: AgentContext) -> AgentResult:
        request = ctx.request
        if not request.needs_charging:
            return placeholder_result(
                AgentName.ENERGY,
                "燃油车无需补能规划，未生成补能点。",
                round=ctx.round,
            )

        if (
            gateway.commercial_charging_configured()
            and gateway.amap_configured()
        ):
            try:
                point = gateway.amap_geocode(request.destination)
                stations = gateway.commercial_charging_nearby(
                    point, radius_km=50, limit=5
                )
            except ToolUnavailableError as exc:
                logger.info("商业充电网络不可用，回退高德 POI：%s", exc)
            except ToolError as exc:
                logger.warning("商业充电网络查询失败，回退高德 POI：%s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "商业充电网络查询异常（%s）。", type(exc).__name__
                )
            else:
                if stations:
                    stops = _stops_from_commercial(stations, request)
                    return AgentResult(
                        agent=AgentName.ENERGY,
                        round=ctx.round,
                        status=AgentStatus.OK,
                        summary=(
                            f"已通过商业充电网络获取 {len(stops)} 个充电站"
                            f"（第 {ctx.round} 轮），含实时价格/排队信息。"
                        ),
                        is_placeholder=False,
                        charging_stops=stops,
                        data_sources=["commercial_charging"],
                    )

        fallback_reason = "未配置高德地图"
        if gateway.amap_configured():
            fallback_reason = "高德充电站查询失败或未找到站点"
            try:
                point = gateway.amap_geocode(request.destination)
                pois = gateway.amap_search_charging_stations(
                    point, radius_m=50_000, limit=5
                )
            except ToolUnavailableError as exc:
                logger.info("充电站数据源不可用，使用占位数据：%s", exc)
            except ToolError as exc:
                logger.warning("高德充电站查询失败，使用占位数据：%s", exc)
            except Exception as exc:  # noqa: BLE001 - Agent 边界统一降级
                logger.warning(
                    "高德充电站查询异常，使用占位数据（%s）。",
                    type(exc).__name__,
                )
            else:
                if pois:
                    stops = _stops_from_pois(pois, request)
                    return AgentResult(
                        agent=AgentName.ENERGY,
                        round=ctx.round,
                        status=AgentStatus.OK,
                        summary=(
                            f"已通过高德 POI 获取 {len(stops)} 个目的地周边充电站"
                            f"（第 {ctx.round} 轮）；实时价格/排队未接入。"
                        ),
                        is_placeholder=False,
                        charging_stops=stops,
                        data_sources=["amap_poi"],
                    )

        step = 2 if request.vehicle.energy_type is EnergyType.EV else 3
        stops: list[ChargingStop] = []
        for day_index in range(1, request.days + 1, step):
            stops.append(
                ChargingStop(
                    day_index=day_index,
                    name=f"第 {day_index} 天补能点（占位）",
                    arrival_soc=round(max(0.12, 0.35 - 0.05 * (ctx.round - 1)), 2),
                    target_soc=0.85,
                    charge_minutes=30 + day_index * 5,
                    queue_probability=round(
                        min(0.85, 0.20 + 0.05 * ctx.round + 0.03 * day_index), 2
                    ),
                    price_per_kwh=round(1.20 + 0.08 * day_index, 2),
                    source="placeholder",
                    notes="占位估算；实时价格/排队/可用性未接入",
                )
            )

        mode = "纯电" if request.vehicle.energy_type is EnergyType.EV else "插混"
        summary = (
            f"{fallback_reason}，为{mode}车型生成 {len(stops)} 个占位补能点"
            f"（第 {ctx.round} 轮）。"
        )
        return placeholder_result(
            AgentName.ENERGY,
            summary,
            round=ctx.round,
            charging_stops=stops,
        )


def _stops_from_commercial(
    stations: list[ChargingStation],
    request: TripRequest,
) -> list[ChargingStop]:
    stops: list[ChargingStop] = []
    for station in stations[:3]:
        notes = station.notes
        if station.available is not None:
            notes = f"{notes}；可用枪数：{station.available}".strip("；")
        stops.append(
            ChargingStop(
                day_index=request.days,
                name=station.name,
                location=station.location,
                arrival_soc=0.2,
                target_soc=0.8,
                charge_minutes=station.charge_minutes,
                queue_probability=station.queue_probability,
                price_per_kwh=station.price_per_kwh,
                source=station.source,
                notes=notes,
            )
        )
    return stops


def _stops_from_pois(
    pois: list[AmapPoi],
    request: TripRequest,
) -> list[ChargingStop]:
    stops: list[ChargingStop] = []
    for poi in pois[:3]:
        distance_note = ""
        if poi.distance_m is not None:
            distance_note = f"距目的地约 {poi.distance_m / 1000:.1f} km；"
        stops.append(
            ChargingStop(
                day_index=request.days,
                name=poi.name,
                location=poi.location,
                arrival_soc=0.2,
                target_soc=0.8,
                charge_minutes=30,
                queue_probability=None,
                price_per_kwh=None,
                source="amap_poi",
                notes=(
                    f"{distance_note}高德 POI；充电时长与电量目标为估算，"
                    "实时价格/排队/可用性未接入。"
                ),
            )
        )
    return stops
