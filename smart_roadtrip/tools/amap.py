"""高德地图 API 封装：地理编码与驾车路线。

当前由 RouteAgent / WeatherAgent 直接调用；后续可通过 tools/mcp/client.py
注册为 MCP 工具。未配置 ``AMAP_API_KEY`` 时抛 ``ToolUnavailableError``，
由 Agent 降级到占位数据。
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import GeoPoint
from smart_roadtrip.tools.base import (
    ToolResponseError,
    get_client,
    get_json,
    require_key,
)

GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"
PLACE_AROUND_URL = "https://restapi.amap.com/v3/place/around"


class AmapRoute(BaseModel):
    """高德驾车路线的精简结果。"""

    name: str = "高德路线"
    distance_km: float = Field(ge=0.0)
    duration_min: int = Field(ge=0)
    tolls_cny: float = Field(default=0.0, ge=0.0)
    polyline: list[GeoPoint] = Field(default_factory=list)


class AmapPoi(BaseModel):
    """高德周边搜索返回的 POI。"""

    poi_id: str = ""
    name: str
    address: str = ""
    location: GeoPoint | None = None
    tel: str = ""
    poi_type: str = ""
    distance_m: float | None = None


def is_configured() -> bool:
    return bool(config.AMAP_API_KEY)


def geocode(
    address: str,
    *,
    client: httpx.Client | None = None,
) -> GeoPoint:
    """把地名转换为经纬度。"""

    require_key(config.AMAP_API_KEY, "AMAP_API_KEY")
    if client is not None:
        return _geocode(client, address)
    with get_client() as http:
        return _geocode(http, address)


def route_plan(
    origin: str,
    destination: str,
    *,
    client: httpx.Client | None = None,
) -> list[AmapRoute]:
    """规划起点到终点的驾车路线，返回高德给出的候选路径。"""

    require_key(config.AMAP_API_KEY, "AMAP_API_KEY")
    if client is not None:
        return _route_plan(client, origin, destination)
    with get_client() as http:
        return _route_plan(http, origin, destination)


def search_nearby(
    point: GeoPoint,
    *,
    keywords: str | None = None,
    types: str | None = None,
    radius_m: int = 50_000,
    limit: int = 10,
    client: httpx.Client | None = None,
) -> list[AmapPoi]:
    """按中心点做周边 POI 搜索。

    高德个人非商业账号的 POI 搜索免费额度有限（约 5000 次/月，以控制台为准），
    调用方应避免在循环里高频查询。
    """

    require_key(config.AMAP_API_KEY, "AMAP_API_KEY")
    if not keywords and not types:
        raise ToolResponseError("周边搜索必须提供 keywords 或 types。")

    radius_m = max(1, min(int(radius_m), 50_000))
    limit = max(1, min(int(limit), 25))

    if client is not None:
        return _search_nearby(client, point, keywords, types, radius_m, limit)
    with get_client() as http:
        return _search_nearby(http, point, keywords, types, radius_m, limit)


def search_charging_stations(
    point: GeoPoint,
    *,
    radius_m: int = 50_000,
    limit: int = 10,
    client: httpx.Client | None = None,
) -> list[AmapPoi]:
    """搜索充电站（高德 POI 类型 011100）。"""

    return search_nearby(
        point,
        types="011100",
        radius_m=radius_m,
        limit=limit,
        client=client,
    )


def search_hospitals(
    point: GeoPoint,
    *,
    radius_m: int = 50_000,
    limit: int = 5,
    client: httpx.Client | None = None,
) -> list[AmapPoi]:
    return search_nearby(
        point, keywords="医院", radius_m=radius_m, limit=limit, client=client
    )


def search_pharmacies(
    point: GeoPoint,
    *,
    radius_m: int = 50_000,
    limit: int = 5,
    client: httpx.Client | None = None,
) -> list[AmapPoi]:
    return search_nearby(
        point, keywords="药店", radius_m=radius_m, limit=limit, client=client
    )


def search_repair_shops(
    point: GeoPoint,
    *,
    radius_m: int = 50_000,
    limit: int = 5,
    client: httpx.Client | None = None,
) -> list[AmapPoi]:
    return search_nearby(
        point, keywords="汽车维修", radius_m=radius_m, limit=limit, client=client
    )


def _geocode(client: httpx.Client, address: str) -> GeoPoint:
    payload = get_json(
        client,
        GEOCODE_URL,
        params={"address": address, "key": config.AMAP_API_KEY},
    )
    _raise_for_amap_status(payload)

    geocodes = payload.get("geocodes") or []
    if not isinstance(geocodes, list) or not geocodes:
        raise ToolResponseError(f"高德未找到地址「{address}」。")

    item = geocodes[0]
    location = item.get("location") or ""
    try:
        lng_text, lat_text = location.split(",", maxsplit=1)
        lng, lat = float(lng_text), float(lat_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ToolResponseError("高德返回的坐标格式不正确。") from exc

    return GeoPoint(
        name=item.get("formatted_address") or item.get("city") or address,
        lng=lng,
        lat=lat,
    )


def _route_plan(
    client: httpx.Client,
    origin: str,
    destination: str,
) -> list[AmapRoute]:
    origin_point = _geocode(client, origin)
    destination_point = _geocode(client, destination)

    payload = get_json(
        client,
        DRIVING_URL,
        params={
            "origin": f"{origin_point.lng},{origin_point.lat}",
            "destination": f"{destination_point.lng},{destination_point.lat}",
            "strategy": 0,
            "extensions": "all",
            "key": config.AMAP_API_KEY,
        },
    )
    _raise_for_amap_status(payload)

    route = payload.get("route") or {}
    if not isinstance(route, dict):
        raise ToolResponseError("高德返回的路线格式不正确。")
    paths = route.get("paths") or []
    if not isinstance(paths, list) or not paths:
        raise ToolResponseError("高德未返回可用驾车路线。")

    routes: list[AmapRoute] = []
    for index, path in enumerate(paths[:3]):
        distance_km = round(_to_float(path.get("distance")) / 1000, 1)
        duration_min = round(_to_float(path.get("duration")) / 60)
        if distance_km <= 0:
            continue
        routes.append(
            AmapRoute(
                name="高德推荐" if index == 0 else f"高德备选{index}",
                distance_km=distance_km,
                duration_min=duration_min,
                tolls_cny=_to_float(path.get("tolls")),
                polyline=_parse_polyline(path),
            )
        )

    if not routes:
        raise ToolResponseError("高德返回的路线数据无效。")
    return routes


def _raise_for_amap_status(payload: dict[str, Any]) -> None:
    if str(payload.get("status")) == "1":
        return
    info = payload.get("info") or payload.get("infocode") or "未知错误"
    raise ToolResponseError(f"高德 API 返回错误：{info}")


def _search_nearby(
    client: httpx.Client,
    point: GeoPoint,
    keywords: str | None,
    types: str | None,
    radius_m: int,
    limit: int,
) -> list[AmapPoi]:
    params: dict[str, Any] = {
        "location": f"{point.lng},{point.lat}",
        "radius": radius_m,
        "offset": limit,
        "page": 1,
        "extensions": "base",
        "key": config.AMAP_API_KEY,
    }
    if keywords:
        params["keywords"] = keywords
    if types:
        params["types"] = types

    payload = get_json(client, PLACE_AROUND_URL, params=params)
    _raise_for_amap_status(payload)

    pois = payload.get("pois") or []
    if not isinstance(pois, list):
        raise ToolResponseError("高德返回的 POI 列表格式不正确。")

    results: list[AmapPoi] = []
    for item in pois[:limit]:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"))
        if not name:
            continue
        results.append(
            AmapPoi(
                poi_id=_as_text(item.get("id")),
                name=name,
                address=_as_text(item.get("address")),
                location=_parse_location(item.get("location")),
                tel=_as_text(item.get("tel")),
                poi_type=_as_text(item.get("type")),
                distance_m=_to_optional_float(item.get("distance")),
            )
        )
    return results


def _parse_location(value: Any) -> GeoPoint | None:
    text = _as_text(value)
    if not text:
        return None
    try:
        lng_text, lat_text = text.split(",", maxsplit=1)
        return GeoPoint(lng=float(lng_text), lat=float(lat_text))
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value if item not in (None, ""))
    if value is None:
        return ""
    return str(value)


def _parse_polyline(path: dict[str, Any]) -> list[GeoPoint]:
    """把高德 steps 中的 ``lng,lat;...`` 折线合并为去重坐标列表。"""

    points: list[GeoPoint] = []
    steps = path.get("steps") or []
    if not isinstance(steps, list):
        return points

    for step in steps:
        if not isinstance(step, dict):
            continue
        text = step.get("polyline")
        if not isinstance(text, str):
            continue
        for pair in text.split(";"):
            try:
                lng_text, lat_text = pair.split(",", maxsplit=1)
                point = GeoPoint(lng=float(lng_text), lat=float(lat_text))
            except (TypeError, ValueError):
                continue
            if not points or points[-1] != point:
                points.append(point)
    return points


def _to_optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
