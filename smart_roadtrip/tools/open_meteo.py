"""Open-Meteo 免费天气 API（无需 API key）。

免费非商业使用，官方 fair use 约 1 万次/天；数据许可为 CC BY 4.0，
使用时需注明来源。天气预报最长 16 天。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import GeoPoint
from smart_roadtrip.tools.base import (
    ToolResponseError,
    ToolUnavailableError,
    get_client,
    get_json,
)
from smart_roadtrip.tools.weather_common import DailyForecast

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_FORECAST_DAYS = 16
ATTRIBUTION = "天气数据由 Open-Meteo 提供（CC BY 4.0）"

_WMO_CONDITIONS: dict[int, tuple[str, float]] = {
    0: ("晴", 0.05),
    1: ("晴间多云", 0.10),
    2: ("多云", 0.15),
    3: ("阴", 0.20),
    45: ("雾", 0.20),
    48: ("雾凇", 0.20),
    51: ("毛毛雨", 0.40),
    53: ("毛毛雨", 0.50),
    55: ("毛毛雨", 0.60),
    56: ("冻毛毛雨", 0.60),
    57: ("冻毛毛雨", 0.65),
    61: ("小雨", 0.60),
    63: ("中雨", 0.70),
    65: ("大雨", 0.80),
    66: ("冻雨", 0.75),
    67: ("冻雨", 0.85),
    71: ("小雪", 0.60),
    73: ("中雪", 0.70),
    75: ("大雪", 0.80),
    77: ("米雪", 0.60),
    80: ("阵雨", 0.60),
    81: ("强阵雨", 0.70),
    82: ("暴雨", 0.85),
    85: ("阵雪", 0.70),
    86: ("强阵雪", 0.80),
    95: ("雷阵雨", 0.80),
    96: ("雷阵雨伴冰雹", 0.85),
    99: ("强雷暴伴冰雹", 0.90),
}


def daily_weather(
    point: GeoPoint,
    *,
    days: int = MAX_FORECAST_DAYS,
    client: httpx.Client | None = None,
) -> list[DailyForecast]:
    """获取从今天起未来 N 天的逐日预报（最多 16 天）。"""

    if client is None and not config.OPEN_METEO_ENABLED:
        raise ToolUnavailableError("Open-Meteo 已在当前配置中禁用。")

    days = max(1, min(int(days), MAX_FORECAST_DAYS))
    if client is not None:
        return _daily_weather(client, point, days)
    with get_client() as http:
        return _daily_weather(http, point, days)


def _daily_weather(
    client: httpx.Client,
    point: GeoPoint,
    days: int,
) -> list[DailyForecast]:
    payload = get_json(
        client,
        FORECAST_URL,
        params={
            "latitude": point.lat,
            "longitude": point.lng,
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max"
            ),
            "timezone": "Asia/Shanghai",
            "forecast_days": days,
        },
    )
    if payload.get("error"):
        raise ToolResponseError(
            f"Open-Meteo 返回错误：{payload.get('reason') or '未知错误'}"
        )

    daily = payload.get("daily") or {}
    if not isinstance(daily, dict):
        raise ToolResponseError("Open-Meteo 返回格式不正确。")

    times = daily.get("time") or []
    if not isinstance(times, list):
        raise ToolResponseError("Open-Meteo 时间序列格式不正确。")

    codes = _as_list(daily.get("weather_code"))
    highs = _as_list(daily.get("temperature_2m_max"))
    lows = _as_list(daily.get("temperature_2m_min"))
    probabilities = _as_list(daily.get("precipitation_probability_max"))

    forecasts: list[DailyForecast] = []
    for index, date_text in enumerate(times[:days]):
        try:
            day_date = date.fromisoformat(str(date_text))
        except (TypeError, ValueError):
            continue

        high = _to_float_or_none(_at(highs, index))
        low = _to_float_or_none(_at(lows, index))
        if high is None or low is None:
            continue

        code = _to_int_or_none(_at(codes, index))
        condition, fallback_probability = _WMO_CONDITIONS.get(
            code if code is not None else -1, ("未知", 0.10)
        )
        probability = _to_float_or_none(_at(probabilities, index))
        if probability is not None:
            probability = max(0.0, min(probability / 100, 1.0))
        else:
            probability = fallback_probability

        forecasts.append(
            DailyForecast(
                date=day_date,
                condition=condition,
                temp_low_c=low,
                temp_high_c=high,
                precipitation_probability=probability,
                warning=_warning_for(code, condition),
            )
        )

    if not forecasts:
        raise ToolResponseError("Open-Meteo 未返回可用日数据。")
    return forecasts


def _warning_for(code: int | None, condition: str) -> str | None:
    if code is None or code <= 3:
        return None
    if code in (45, 48):
        return f"Open-Meteo：{condition}，请注意行车安全。"
    return f"Open-Meteo：{condition}，请复核路况与装备。"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _at(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None


def _to_float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
