"""救援与医疗商业供应商适配。

未配置 ``RESCUE_MEDICAL_API_BASE_URL`` + ``RESCUE_MEDICAL_API_KEY`` 时
:func:`get_provider` 返回 ``None``，SafetyAgent 回退到高德 POI。

供应商契约（JSON）::

    GET {base_url}/rescue-resources?lat=..&lng=..&radius_km=..&limit=..
    Authorization: Bearer <RESCUE_MEDICAL_API_KEY>

    {
      "resources": [
        {
          "name": "稻城县人民医院",
          "category": "医院",
          "lat": 28.44,
          "lng": 100.31,
          "address": "稻城县",
          "phone": "0836-120",
          "notes": "可选说明"
        }
      ]
    }
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import GeoPoint, RescueResource
from smart_roadtrip.tools.base import ToolResponseError, get_client, get_json


class RescueProvider(Protocol):
    def nearby(
        self,
        point: GeoPoint,
        *,
        radius_km: float = 50.0,
        limit: int = 9,
        client: httpx.Client | None = None,
    ) -> list[RescueResource]: ...


class HttpRescueProvider:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def nearby(
        self,
        point: GeoPoint,
        *,
        radius_km: float = 50.0,
        limit: int = 9,
        client: httpx.Client | None = None,
    ) -> list[RescueResource]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {
            "lat": point.lat,
            "lng": point.lng,
            "radius_km": max(0.1, min(float(radius_km), 100.0)),
            "limit": max(1, min(int(limit), 30)),
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
    ) -> list[RescueResource]:
        payload = get_json(
            client,
            f"{self.base_url}/rescue-resources",
            params=params,
            headers=headers,
        )
        resources = payload.get("resources") or []
        if not isinstance(resources, list):
            raise ToolResponseError("救援供应商返回的 resources 格式不正确。")

        results: list[RescueResource] = []
        for item in resources[: int(params["limit"])]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            results.append(
                RescueResource(
                    name=str(name),
                    category=str(item.get("category") or "救援资源"),
                    location=_location(item),
                    address=str(item.get("address") or ""),
                    phone=str(item.get("phone") or ""),
                    source="commercial_rescue",
                    notes=str(item.get("notes") or ""),
                )
            )
        return results


def is_configured() -> bool:
    return bool(
        config.RESCUE_MEDICAL_API_BASE_URL
        and config.RESCUE_MEDICAL_API_KEY
    )


def get_provider() -> RescueProvider | None:
    if not is_configured():
        return None
    return HttpRescueProvider(
        config.RESCUE_MEDICAL_API_BASE_URL,
        config.RESCUE_MEDICAL_API_KEY,
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


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
