"""商业救援/医疗供应商适配测试。"""

from __future__ import annotations

import httpx

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import GeoPoint
from smart_roadtrip.tools import rescue_medical

_POINT = GeoPoint(name="稻城亚丁", lng=100.3, lat=28.4)


def test_provider_is_none_when_unconfigured() -> None:
    assert rescue_medical.get_provider() is None


def test_http_provider_parses_resources(monkeypatch) -> None:
    monkeypatch.setattr(
        config, "RESCUE_MEDICAL_API_BASE_URL", "https://example.com"
    )
    monkeypatch.setattr(config, "RESCUE_MEDICAL_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "resources": [
                    {
                        "name": "稻城县人民医院",
                        "category": "医院",
                        "lat": 28.45,
                        "lng": 100.32,
                        "address": "稻城县",
                        "phone": "0836-120",
                        "notes": "24 小时急诊",
                    }
                ]
            },
        )

    provider = rescue_medical.get_provider()
    assert provider is not None
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resources = provider.nearby(_POINT, radius_km=50, limit=5, client=client)

    assert len(resources) == 1
    resource = resources[0]
    assert resource.name == "稻城县人民医院"
    assert resource.category == "医院"
    assert resource.address == "稻城县"
    assert resource.phone == "0836-120"
    assert resource.location is not None
    assert resource.location.lat == 28.45
    assert resource.source == "commercial_rescue"
