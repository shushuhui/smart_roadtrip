"""高德工具测试：使用 httpx.MockTransport，不访问真实网络。"""

from __future__ import annotations

import httpx
import pytest

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import GeoPoint
from smart_roadtrip.tools import amap
from smart_roadtrip.tools.base import ToolError, ToolUnavailableError


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "geocode/geo" in url:
        address = request.url.params.get("address")
        locations = {
            "成都": ("104.066541,30.572269", "四川省成都市"),
            "稻城亚丁": ("100.297000,28.435000", "四川省甘孜州稻城县亚丁村"),
        }
        if address not in locations:
            return httpx.Response(200, json={"status": "1", "info": "OK", "geocodes": []})
        location, formatted = locations[address]
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "geocodes": [{"formatted_address": formatted, "location": location}],
            },
        )

    if "direction/driving" in url:
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "route": {
                    "paths": [
                        {
                            "distance": "1200000",
                            "duration": "72000",
                            "tolls": "300",
                            "steps": [
                                {"polyline": "104.000000,30.500000;104.100000,30.600000"},
                                {"polyline": "104.100000,30.600000;104.200000,30.700000"},
                            ],
                        },
                        {"distance": "1100000", "duration": "78000", "tolls": "0"},
                    ]
                },
            },
        )

    if "place/around" in url:
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "pois": [
                    {
                        "id": "p1",
                        "name": "稻城县城充电站",
                        "address": "稻城县金珠镇",
                        "location": "100.300000,28.440000",
                        "tel": "0836-1234567",
                        "type": "汽车服务;充电站",
                        "distance": "1200",
                    },
                    {
                        "id": "p2",
                        "name": "亚丁游客中心充电站",
                        "address": [],
                        "location": "100.310000,28.450000",
                        "tel": [],
                        "type": "汽车服务;充电站",
                        "distance": "3500",
                    },
                ],
            },
        )

    return httpx.Response(404, json={})


def test_geocode_and_route_plan(monkeypatch) -> None:
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")

    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        point = amap.geocode("成都", client=client)
        assert point.name == "四川省成都市"
        assert point.lng == pytest.approx(104.066541)
        assert point.lat == pytest.approx(30.572269)

        routes = amap.route_plan("成都", "稻城亚丁", client=client)

    assert len(routes) == 2
    assert routes[0].name == "高德推荐"
    assert routes[0].distance_km == 1200.0
    assert routes[0].duration_min == 1200
    assert routes[0].tolls_cny == 300.0
    assert len(routes[0].polyline) == 3
    assert routes[0].polyline[0].lng == pytest.approx(104.0)
    assert routes[0].polyline[-1].lat == pytest.approx(30.7)
    assert routes[1].name == "高德备选1"


def test_missing_key_raises_unavailable() -> None:
    with pytest.raises(ToolUnavailableError, match="AMAP_API_KEY"):
        amap.geocode("成都")


def test_amap_error_status_raises(monkeypatch) -> None:
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "0", "info": "INVALID_USER_KEY"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="INVALID_USER_KEY"):
            amap.geocode("成都", client=client)


def test_unknown_address_raises(monkeypatch) -> None:
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")

    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        with pytest.raises(ToolError, match="未找到地址"):
            amap.geocode("不存在的地方", client=client)


def test_search_charging_stations(monkeypatch) -> None:
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")
    point = GeoPoint(name="稻城亚丁", lng=100.297, lat=28.435)

    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        pois = amap.search_charging_stations(point, limit=2, client=client)

    assert len(pois) == 2
    assert pois[0].name == "稻城县城充电站"
    assert pois[0].address == "稻城县金珠镇"
    assert pois[0].location is not None
    assert pois[0].location.lng == pytest.approx(100.3)
    assert pois[0].tel == "0836-1234567"
    assert pois[0].distance_m == 1200
    assert pois[1].address == ""
    assert pois[1].tel == ""


def test_search_hospitals_sends_keywords(monkeypatch) -> None:
    monkeypatch.setattr(config, "AMAP_API_KEY", "test-key")
    requests: list[httpx.Request] = []
    point = GeoPoint(name="稻城亚丁", lng=100.297, lat=28.435)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "1", "pois": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert amap.search_hospitals(point, client=client) == []

    assert requests[0].url.params.get("keywords") == "医院"
    assert requests[0].url.params.get("location") == "100.297,28.435"
