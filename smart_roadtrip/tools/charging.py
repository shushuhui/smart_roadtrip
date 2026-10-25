"""充电网络商业供应商适配。

未配置 ``CHARGING_API_BASE_URL`` + ``CHARGING_API_KEY`` 时
:func:`get_provider` 返回 ``None``，EnergyAgent 回退到高德 POI / 占位数据。

供应商契约（JSON）::

    GET {base_url}/charging-stations?lat=..&lng=..&radius_km=..&limit=..
    Authorization: Bearer <CHARGING_API_KEY>

    {
      "stations": [
        {
          "name": "站点名",
          "lat": 30.1,
          "lng": 104.2,
          "price_per_kwh": 1.2,
          "queue_probability": 0.3,
          "available": 4,
          "charge_minutes": 30,
          "notes": "可选说明"
        }
      ]
    }
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import GeoPoint
from smart_roadtrip.tools.base import ToolResponseError, get_client, get_json


class ChargingStation(BaseModel):
    name: str
    location: GeoPoint | None = None
    price_per_kwh: float | None = Field(default=None, ge=0.0)
    queue_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    available: int | None = Field(default=None, ge=0)
    charge_minutes: int = Field(default=30, ge=0)
    source: str = "commercial_charging"
    notes: str = ""


class ChargingProvider(Protocol):
    def nearby(
        self,
        point: GeoPoint,
        *,
        radius_km: float = 50.0,
        limit: int = 10,
        client: httpx.Client | None = None,
    ) -> list[ChargingStation]: ...


class HttpChargingProvider:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def nearby(
        self,
        point: GeoPoint,
        *,
        radius_km: float = 50.0,
        limit: int = 10,
        client: httpx.Client | None = None,
    ) -> list[ChargingStation]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {
            "lat": point.lat,
            "lng": point.lng,
            "radius_km": max(0.1, min(float(radius_km), 100.0)),
            "limit": max(1, min(int(limit), 50)),
        }
        if client is not None:
            return self._nearby(client, params, headers)
        with get_client() as http:
            return self._nearby(http, params, headers)

    def _nearby(
        self,
        client: httpx.Client,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> list[ChargingStation]:
        payload = get_json(
            client,
            f"{self.base_url}/charging-stations",
            params=params,
            headers=headers,
        )
        stations = payload.get("stations") or []
        if not isinstance(stations, list):
            raise ToolResponseError("充电供应商返回的 stations 格式不正确。")

        results: list[ChargingStation] = []
        for item in stations[: int(params["limit"])]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            results.append(
                ChargingStation(
                    name=str(name),
                    location=_location(item),
                    price_per_kwh=_optional_float(item.get("price_per_kwh")),
                    queue_probability=_probability(
                        item.get("queue_probability")
                    ),
                    available=_optional_int(item.get("available")),
                    charge_minutes=_optional_int(item.get("charge_minutes"))
                    or 30,
                    notes=str(item.get("notes") or ""),
                )
            )
        return results


def is_configured() -> bool:
    return bool(config.CHARGING_API_BASE_URL and config.CHARGING_API_KEY)


def get_provider() -> ChargingProvider | None:
    if not is_configured():
        return None
    return HttpChargingProvider(
        config.CHARGING_API_BASE_URL, config.CHARGING_API_KEY
    )


def _location(item: dict[str, Any]) -> GeoPoint | None:
    location = item.get("location")
    if isinstance(location, dict):
        lat = _optional_float(location.get("lat"))
        lng = _optional_float(location.get("lng"))
    else:
        lat = _optional_float(item.get("lat"))
        lng = _optional_float(item.get("lng"))
    if lat is None or lng is None:
        return None
    return GeoPoint(
        name=str(item.get("address") or ""),
        lat=lat,
        lng=lng,
    )


def _probability(value: Any) -> float | None:
    parsed = _optional_float(value)
    if parsed is None:
        return None
    if 1 < parsed <= 100:
        parsed /= 100
    return max(0.0, min(parsed, 1.0))


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
