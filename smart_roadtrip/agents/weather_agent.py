"""WeatherAgent：天气排程与预警。

优先通过高德地理编码拿到目的地坐标，然后依次尝试：
1. 彩云天气（配置了 ``CAIYUN_API_KEY`` 时）；
2. Open-Meteo（免费、无需 key，最多 16 天）。

两者都不可用或预报不覆盖行程日期时，按天生成确定性占位天气窗口。
"""

from __future__ import annotations

import logging

from smart_roadtrip.agents.base import AgentContext, BaseAgent, placeholder_result
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    AgentStatus,
    GeoPoint,
    TripRequest,
    WeatherWindow,
)
from smart_roadtrip.tools import gateway
from smart_roadtrip.tools.base import ToolError, ToolUnavailableError
from smart_roadtrip.tools.weather_common import DailyForecast

logger = logging.getLogger(__name__)

_CONDITIONS = (
    ("晴", 0.10, 16.0, 26.0),
    ("多云", 0.20, 15.0, 24.0),
    ("小雨", 0.60, 13.0, 20.0),
    ("晴间多云", 0.15, 17.0, 27.0),
)


class WeatherAgent(BaseAgent):
    name = AgentName.WEATHER

    def _run(self, ctx: AgentContext) -> AgentResult:
        request = ctx.request
        fallback_reason = "未配置高德地图（无法解析目的地坐标）"

        if gateway.amap_configured():
            fallback_reason = "高德地理编码失败"
            point = None
            try:
                point = gateway.amap_geocode(request.destination)
            except ToolUnavailableError as exc:
                logger.info("高德地理编码不可用：%s", exc)
            except ToolError as exc:
                logger.warning("高德地理编码失败：%s", exc)
            except Exception as exc:  # noqa: BLE001 - Agent 边界统一降级
                logger.warning(
                    "高德地理编码异常（%s）。", type(exc).__name__
                )

            if point is not None:
                fallback_reason = "天气数据源查询失败或预报未覆盖行程日期"

                caiyun_result: AgentResult | None = None
                if gateway.caiyun_configured():
                    caiyun_result = _try_caiyun(point, request, ctx)
                    if (
                        caiyun_result is not None
                        and len(caiyun_result.weather_windows) >= request.days
                    ):
                        return caiyun_result

                open_meteo_result = _try_open_meteo(point, request, ctx)
                if open_meteo_result is not None and (
                    caiyun_result is None
                    or len(open_meteo_result.weather_windows)
                    > len(caiyun_result.weather_windows)
                ):
                    return open_meteo_result
                if caiyun_result is not None:
                    return caiyun_result

                logger.info("天气数据源均未覆盖行程日期，使用占位数据。")

        days = request.days
        windows: list[WeatherWindow] = []
        for day_index in range(1, days + 1):
            condition, precipitation, low, high = _CONDITIONS[
                (day_index + ctx.round - 1) % len(_CONDITIONS)
            ]
            warning = None
            if condition == "小雨":
                warning = "占位提醒：降水概率偏高，出发前请复核实时天气与山区路况。"
            elif day_index == days:
                warning = "占位提醒：返程日请预留机动时间，避免夜间驾驶。"
            windows.append(
                WeatherWindow(
                    day_index=day_index,
                    condition=condition,
                    temp_low_c=low,
                    temp_high_c=high,
                    precipitation_probability=precipitation,
                    warning=warning,
                )
            )

        summary = (
            f"{fallback_reason}，生成 {days} 天占位天气窗口（第 {ctx.round} 轮）。"
        )
        return placeholder_result(
            AgentName.WEATHER,
            summary,
            round=ctx.round,
            weather_windows=windows,
        )


def _try_caiyun(
    point: GeoPoint,
    request: TripRequest,
    ctx: AgentContext,
) -> AgentResult | None:
    try:
        forecast = gateway.caiyun_daily_weather(
            point, days=gateway.CAIYUN_MAX_DAYS
        )
    except ToolUnavailableError as exc:
        logger.info("彩云天气不可用：%s", exc)
        return None
    except ToolError as exc:
        logger.warning("彩云天气查询失败：%s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - Agent 边界统一降级
        logger.warning("彩云天气查询异常（%s）。", type(exc).__name__)
        return None

    windows = _map_forecast(forecast, request)
    if not windows:
        logger.info("彩云预报未覆盖行程日期。")
        return None

    return AgentResult(
        agent=AgentName.WEATHER,
        round=ctx.round,
        status=AgentStatus.OK,
        summary=(
            f"已通过高德+彩云获取 {len(windows)} 天天气"
            f"（第 {ctx.round} 轮）；降水概率按天气现象估算。"
        ),
        is_placeholder=False,
        weather_windows=windows,
        data_sources=["amap_geocode", "caiyun"],
    )


def _try_open_meteo(
    point: GeoPoint,
    request: TripRequest,
    ctx: AgentContext,
) -> AgentResult | None:
    try:
        forecast = gateway.open_meteo_daily_weather(
            point, days=gateway.OPEN_METEO_MAX_DAYS
        )
    except ToolError as exc:
        logger.warning("Open-Meteo 查询失败：%s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - Agent 边界统一降级
        logger.warning("Open-Meteo 查询异常（%s）。", type(exc).__name__)
        return None

    windows = _map_forecast(forecast, request)
    if not windows:
        logger.info("Open-Meteo 预报未覆盖行程日期。")
        return None

    return AgentResult(
        agent=AgentName.WEATHER,
        round=ctx.round,
        status=AgentStatus.OK,
        summary=(
            f"已通过高德+Open-Meteo 获取 {len(windows)} 天天气"
            f"（第 {ctx.round} 轮，无需 key）；降水概率来自 Open-Meteo。"
        ),
        is_placeholder=False,
        weather_windows=windows,
        data_sources=["amap_geocode", "open_meteo"],
    )


def _map_forecast(
    forecast: list[DailyForecast],
    request: TripRequest,
) -> list[WeatherWindow]:
    """把逐日预报按行程日期映射为 day_index。"""

    windows: list[WeatherWindow] = []
    for day in forecast:
        offset = (day.date - request.start_date).days
        if 0 <= offset < request.days:
            windows.append(
                WeatherWindow(
                    day_index=offset + 1,
                    condition=day.condition,
                    temp_low_c=day.temp_low_c,
                    temp_high_c=day.temp_high_c,
                    precipitation_probability=day.precipitation_probability,
                    warning=day.warning,
                )
            )
    return windows
