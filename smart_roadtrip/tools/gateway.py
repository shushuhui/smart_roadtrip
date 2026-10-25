"""统一工具网关。

``TOOL_BACKEND=direct`` 时直接调用进程内 tools/ 函数；``mcp`` 时通过持久化
MCP stdio 会话调用 ``tools/mcp/server.py`` 暴露的工具。Agent 只依赖本模块，
便于在两种后端之间切换。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from smart_roadtrip import config
from smart_roadtrip.rag import retriever
from smart_roadtrip.schemas.trip import GeoPoint, KnowledgeSnippet, RescueResource
from smart_roadtrip.tools import amap, caiyun, charging, open_meteo, rescue_medical
from smart_roadtrip.tools.base import ToolError, ToolUnavailableError
from smart_roadtrip.tools.charging import ChargingStation
from smart_roadtrip.tools.weather_common import DailyForecast

logger = logging.getLogger(__name__)

CAIYUN_MAX_DAYS = caiyun.MAX_FORECAST_DAYS
OPEN_METEO_MAX_DAYS = open_meteo.MAX_FORECAST_DAYS

_status_lock = threading.Lock()
_status_cache: dict[str, Any] = {"value": None, "ts": 0.0}
_STATUS_TTL_SECONDS = 5.0


def tool_backend() -> str:
    return (config.TOOL_BACKEND or "direct").strip().lower()


def tool_status() -> dict[str, Any]:
    """返回数据源与 RAG 状态；MCP 模式下缓存 5 秒。"""

    if tool_backend() != "mcp":
        return {
            "amap_configured": amap.is_configured(),
            "caiyun_configured": caiyun.is_configured(),
            "open_meteo_enabled": config.OPEN_METEO_ENABLED,
            "rag_enabled": config.RAG_ENABLED,
            "rag_index_available": retriever.is_available(),
            "commercial_charging_configured": charging.is_configured(),
            "commercial_rescue_configured": rescue_medical.is_configured(),
        }

    now = time.monotonic()
    with _status_lock:
        cached = _status_cache["value"]
        if cached is not None and now - _status_cache["ts"] < _STATUS_TTL_SECONDS:
            return dict(cached)

    value = _mcp_call("tool_status", {})
    with _status_lock:
        _status_cache["value"] = value
        _status_cache["ts"] = time.monotonic()
    return dict(value)


def amap_configured() -> bool:
    return _status_flag("amap_configured")


def caiyun_configured() -> bool:
    return _status_flag("caiyun_configured")


def open_meteo_enabled() -> bool:
    return _status_flag("open_meteo_enabled")


def amap_geocode(address: str) -> GeoPoint:
    if tool_backend() != "mcp":
        return amap.geocode(address)
    return GeoPoint.model_validate(
        _mcp_call("amap_geocode", {"address": address})
    )


def amap_route_plan(origin: str, destination: str) -> list[amap.AmapRoute]:
    if tool_backend() != "mcp":
        return amap.route_plan(origin, destination)
    payload = _mcp_call(
        "amap_route", {"origin": origin, "destination": destination}
    )
    return [amap.AmapRoute.model_validate(item) for item in payload]


def amap_search_nearby(
    point: GeoPoint,
    *,
    keywords: str | None = None,
    types: str | None = None,
    radius_m: int = 50_000,
    limit: int = 10,
) -> list[amap.AmapPoi]:
    if tool_backend() != "mcp":
        return amap.search_nearby(
            point,
            keywords=keywords,
            types=types,
            radius_m=radius_m,
            limit=limit,
        )
    payload = _mcp_call(
        "amap_nearby",
        {
            "latitude": point.lat,
            "longitude": point.lng,
            "keywords": keywords,
            "types": types,
            "radius_m": radius_m,
            "limit": limit,
        },
    )
    return [amap.AmapPoi.model_validate(item) for item in payload]


def amap_search_charging_stations(
    point: GeoPoint,
    *,
    radius_m: int = 50_000,
    limit: int = 10,
) -> list[amap.AmapPoi]:
    if tool_backend() != "mcp":
        return amap.search_charging_stations(
            point, radius_m=radius_m, limit=limit
        )
    return amap_search_nearby(
        point, types="011100", radius_m=radius_m, limit=limit
    )


def amap_search_hospitals(
    point: GeoPoint,
    *,
    radius_m: int = 50_000,
    limit: int = 5,
) -> list[amap.AmapPoi]:
    if tool_backend() != "mcp":
        return amap.search_hospitals(point, radius_m=radius_m, limit=limit)
    return amap_search_nearby(
        point, keywords="医院", radius_m=radius_m, limit=limit
    )


def amap_search_pharmacies(
    point: GeoPoint,
    *,
    radius_m: int = 50_000,
    limit: int = 5,
) -> list[amap.AmapPoi]:
    if tool_backend() != "mcp":
        return amap.search_pharmacies(point, radius_m=radius_m, limit=limit)
    return amap_search_nearby(
        point, keywords="药店", radius_m=radius_m, limit=limit
    )


def amap_search_repair_shops(
    point: GeoPoint,
    *,
    radius_m: int = 50_000,
    limit: int = 5,
) -> list[amap.AmapPoi]:
    if tool_backend() != "mcp":
        return amap.search_repair_shops(
            point, radius_m=radius_m, limit=limit
        )
    return amap_search_nearby(
        point, keywords="汽车维修", radius_m=radius_m, limit=limit
    )


def caiyun_daily_weather(
    point: GeoPoint,
    *,
    days: int = CAIYUN_MAX_DAYS,
) -> list[DailyForecast]:
    if tool_backend() != "mcp":
        return caiyun.daily_weather(point, days=days)
    payload = _mcp_call(
        "weather_forecast",
        {
            "latitude": point.lat,
            "longitude": point.lng,
            "provider": "caiyun",
            "days": days,
        },
    )
    return [DailyForecast.model_validate(item) for item in payload]


def open_meteo_daily_weather(
    point: GeoPoint,
    *,
    days: int = OPEN_METEO_MAX_DAYS,
) -> list[DailyForecast]:
    if tool_backend() != "mcp":
        return open_meteo.daily_weather(point, days=days)
    payload = _mcp_call(
        "weather_forecast",
        {
            "latitude": point.lat,
            "longitude": point.lng,
            "provider": "open_meteo",
            "days": days,
        },
    )
    return [DailyForecast.model_validate(item) for item in payload]


def commercial_charging_configured() -> bool:
    return _status_flag("commercial_charging_configured")


def commercial_rescue_configured() -> bool:
    return _status_flag("commercial_rescue_configured")


def commercial_charging_nearby(
    point: GeoPoint,
    *,
    radius_km: float = 50.0,
    limit: int = 10,
) -> list[ChargingStation]:
    if tool_backend() != "mcp":
        provider = charging.get_provider()
        if provider is None:
            raise ToolUnavailableError("未配置商业充电网络。")
        return provider.nearby(point, radius_km=radius_km, limit=limit)

    payload = _mcp_call(
        "charging_nearby",
        {
            "latitude": point.lat,
            "longitude": point.lng,
            "radius_km": radius_km,
            "limit": limit,
        },
    )
    return [ChargingStation.model_validate(item) for item in payload]


def commercial_rescue_nearby(
    point: GeoPoint,
    *,
    radius_km: float = 50.0,
    limit: int = 9,
) -> list[RescueResource]:
    if tool_backend() != "mcp":
        provider = rescue_medical.get_provider()
        if provider is None:
            raise ToolUnavailableError("未配置商业救援网络。")
        return provider.nearby(point, radius_km=radius_km, limit=limit)

    payload = _mcp_call(
        "rescue_nearby",
        {
            "latitude": point.lat,
            "longitude": point.lng,
            "radius_km": radius_km,
            "limit": limit,
        },
    )
    return [RescueResource.model_validate(item) for item in payload]


def rag_retrieve(
    query: str,
    *,
    top_k: int | None = None,
) -> list[KnowledgeSnippet]:
    if tool_backend() != "mcp":
        return retriever.retrieve(query, top_k=top_k)
    arguments: dict[str, Any] = {"query": query}
    if top_k is not None:
        arguments["top_k"] = top_k
    payload = _mcp_call("rag_search", arguments)
    return [KnowledgeSnippet.model_validate(item) for item in payload]


def reset_gateway_cache() -> None:
    """清空状态缓存并关闭持久 MCP 会话（测试/配置切换时使用）。"""

    with _status_lock:
        _status_cache["value"] = None
        _status_cache["ts"] = 0.0
    if tool_backend() == "mcp":
        from smart_roadtrip.tools.mcp.gateway import reset_gateway

        reset_gateway()


def _status_flag(name: str) -> bool:
    try:
        return bool(tool_status().get(name, False))
    except ToolError as exc:
        logger.warning("读取工具状态失败，按未启用以降级：%s", exc)
        return False


def _mcp_call(name: str, arguments: dict[str, Any]) -> Any:
    try:
        from smart_roadtrip.tools.mcp.gateway import (
            MCPToolError,
            MCPUnavailableError,
            get_gateway,
            reset_gateway,
        )
    except Exception as exc:  # noqa: BLE001 - 依赖/导入失败
        raise ToolUnavailableError(
            "MCP 工具不可用（无法导入 MCP gateway）。"
        ) from exc

    try:
        return get_gateway().call_tool(name, arguments)
    except MCPToolError:
        raise
    except MCPUnavailableError as exc:
        logger.warning("MCP 会话不可用，重启后重试一次：%s", exc)
        try:
            reset_gateway()
            return get_gateway().call_tool(name, arguments)
        except MCPToolError:
            raise
        except Exception as retry_exc:  # noqa: BLE001
            raise ToolUnavailableError(
                f"MCP 工具 {name} 重试失败：{type(retry_exc).__name__}"
            ) from retry_exc
    except Exception as exc:  # noqa: BLE001 - 连接/协议错误
        raise ToolUnavailableError(
            f"MCP 工具 {name} 不可用：{type(exc).__name__}"
        ) from exc
