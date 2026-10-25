"""彩云天气 API 封装：逐日天气。

当前由 WeatherAgent 直接调用；后续可通过 tools/mcp/client.py 注册为 MCP 工具。
未配置 ``CAIYUN_API_KEY`` 时抛 ``ToolUnavailableError``，由 Agent 降级。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import GeoPoint
from smart_roadtrip.tools.base import (
    ToolResponseError,
    get_client,
    get_json,
    require_key,
)
from smart_roadtrip.tools.weather_common import DailyForecast

# 兼容旧名称；彩云与 Open-Meteo 共用同一逐日预报模型。
CaiyunDay = DailyForecast

DAILY_URL_TEMPLATE = "https://api.caiyunapp.com/v2.6/{token}/{lng},{lat}/daily.json"
MAX_FORECAST_DAYS = 15

_SKYCON_LABELS = {
    "CLEAR_DAY": "晴",
    "CLEAR_NIGHT": "晴",
    "PARTLY_CLOUDY_DAY": "多云",
    "PARTLY_CLOUDY_NIGHT": "多云",
    "CLOUDY": "阴",
    "LIGHT_HAZE": "轻度雾霾",
    "MODERATE_HAZE": "中度雾霾",
    "HEAVY_HAZE": "重度雾霾",
    "LIGHT_RAIN": "小雨",
    "MODERATE_RAIN": "中雨",
    "HEAVY_RAIN": "大雨",
    "STORM_RAIN": "暴雨",
    "FOG": "雾",
    "LIGHT_SNOW": "小雪",
    "MODERATE_SNOW": "中雪",
    "HEAVY_SNOW": "大雪",
    "STORM_SNOW": "暴雪",
    "DUST": "浮尘",
    "SAND": "沙尘",
    "WIND": "大风",
}


def is_configured() -> bool:
    return bool(config.CAIYUN_API_KEY)


def daily_weather(
    point: GeoPoint,
    *,
    days: int = MAX_FORECAST_DAYS,
    client: httpx.Client | None = None,
) -> list[DailyForecast]:
    """获取从今天起未来 N 天的逐日天气（最多 15 天）。

    彩云日数据不直接给出降水概率，``precipitation_probability`` 是按天气现象
    估算的值，仅用于排序与提示。
    """

    require_key(config.CAIYUN_API_KEY, "CAIYUN_API_KEY")
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
    url = DAILY_URL_TEMPLATE.format(
        token=config.CAIYUN_API_KEY,
        lng=point.lng,
        lat=point.lat,
    )
    payload = get_json(client, url, params={"dailysteps": days, "lang": "zh-CN"})
    if payload.get("status") != "ok":
        raise ToolResponseError(
            f"彩云天气 API 返回错误：{payload.get('status') or payload.get('message') or '未知错误'}"
        )

    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise ToolResponseError("彩云天气返回格式不正确。")
    daily = result.get("daily") or {}
    if not isinstance(daily, dict):
        raise ToolResponseError("彩云天气日数据格式不正确。")
    if daily.get("status") not in (None, "ok"):
        raise ToolResponseError(f"彩云天气日数据不可用：{daily.get('status')}")

    temperature_items = daily.get("temperature") or []
    if not isinstance(temperature_items, list):
        raise ToolResponseError("彩云天气温度数据格式不正确。")
    temperatures = {
        item.get("date"): item
        for item in temperature_items
        if isinstance(item, dict) and item.get("date")
    }

    skycon_items = daily.get("skycon") or []
    if not isinstance(skycon_items, list):
        raise ToolResponseError("彩云天气现象数据格式不正确。")

    forecasts: list[DailyForecast] = []
    for item in skycon_items[:days]:
        if not isinstance(item, dict):
            continue
        date_text = item.get("date")
        try:
            day_date = date.fromisoformat(str(date_text))
        except (TypeError, ValueError):
            continue

        skycon = str(item.get("value") or "UNKNOWN")
        condition = _SKYCON_LABELS.get(skycon, "未知")
        temperature = temperatures.get(date_text) or {}
        forecasts.append(
            DailyForecast(
                date=day_date,
                condition=condition,
                temp_low_c=_to_float(temperature.get("min")),
                temp_high_c=_to_float(temperature.get("max")),
                precipitation_probability=_estimate_probability(skycon),
                warning=_warning_for(skycon, condition),
            )
        )

    if not forecasts:
        raise ToolResponseError("彩云天气未返回可用日数据。")
    return forecasts


def _estimate_probability(skycon: str) -> float:
    if any(token in skycon for token in ("RAIN", "SNOW", "STORM")):
        return 0.7
    if "HAZE" in skycon or skycon in ("CLOUDY", "FOG") or "CLOUD" in skycon:
        return 0.2
    if skycon in ("DUST", "SAND", "WIND"):
        return 0.15
    return 0.05


def _warning_for(skycon: str, condition: str) -> str | None:
    if any(token in skycon for token in ("RAIN", "SNOW", "STORM")):
        return f"彩云天气：{condition}，请复核路况与装备。"
    if "HAZE" in skycon:
        return f"彩云天气：{condition}，敏感人群请减少户外活动。"
    if skycon in ("FOG", "DUST", "SAND", "WIND"):
        return f"彩云天气：{condition}，请注意行车安全。"
    return None


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
