"""商业充电供应商适配测试。"""

from __future__ import annotations

import httpx
import pytest

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import GeoPoint
from smart_roadtrip.tools import charging
from smart_roadtrip.tools.base import ToolError

_POINT = GeoPoint(name="稻城亚丁", lng=100.3, lat=28.4)


def test_provider_is_none_when_unconfigured() -> None:
    assert charging.get_provider() is None


def test_http_provider_parses_stations(monkeypatch) -> None:
    monkeypatch.setattr(config, "CHARGING_API_BASE_URL", "https://example.com")
    monkeypatch.setattr(config, "CHARGING_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "stations": [
                    {
                        "name": "商业充电站",
                        "lat": 28.44,
                        "lng": 100.31,
                        "price_per_kwh": 1.25,
                        "queue_probability": 30,
                        "available": 4,
                        "charge_minutes": 25,
                        "notes": "快充",
                    }
                ]
            },
        )

    provider = charging.get_provider()
    assert provider is not None
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        stations = provider.nearby(_POINT, radius_km=50, limit=5, client=client)

    assert requests
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert len(stations) == 1
    station = stations[0]
    assert station.name == "商业充电站"
    assert station.location is not None
    assert station.location.lat == pytest.approx(28.44)
    assert station.price_per_kwh == pytest.approx(1.25)
    assert station.queue_probability == pytest.approx(0.30)
    assert station.available == 4
    assert station.charge_minutes == 25
    assert station.source == "commercial_charging"


def test_http_provider_error_raises(monkeypatch) -> None:
    monkeypatch.setattr(config, "CHARGING_API_BASE_URL", "https://example.com")
    monkeypatch.setattr(config, "CHARGING_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = charging.get_provider()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError):
            provider.nearby(_POINT, client=client)
