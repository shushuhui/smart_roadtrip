"""Open-Meteo 工具测试：免费、无需 key；使用 MockTransport，不访问真实网络。"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from smart_roadtrip.schemas.trip import GeoPoint
from smart_roadtrip.tools import open_meteo
from smart_roadtrip.tools.base import ToolError, ToolUnavailableError

_POINT = GeoPoint(name="稻城亚丁", lng=100.297, lat=28.435)


def test_daily_weather_parses_forecast() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "open-meteo.com" in str(request.url)
        return httpx.Response(
            200,
            json={
                "daily": {
                    "time": ["2026-11-01", "2026-11-02"],
                    "weather_code": [0, 61],
                    "temperature_2m_max": [18, 15],
                    "temperature_2m_min": [5, 6],
                    "precipitation_probability_max": [10, 80],
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        forecast = open_meteo.daily_weather(_POINT, days=2, client=client)

    assert len(forecast) == 2
    assert forecast[0].date == date(2026, 11, 1)
    assert forecast[0].condition == "晴"
    assert forecast[0].temp_low_c == 5
    assert forecast[0].temp_high_c == 18
    assert forecast[0].precipitation_probability == pytest.approx(0.10)
    assert forecast[0].warning is None
    assert forecast[1].condition == "小雨"
    assert forecast[1].precipitation_probability == pytest.approx(0.80)
    assert forecast[1].warning and "小雨" in forecast[1].warning


def test_disabled_without_client_raises() -> None:
    # conftest 会把 OPEN_METEO_ENABLED 置为 False。
    with pytest.raises(ToolUnavailableError):
        open_meteo.daily_weather(_POINT)


def test_error_payload_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": True, "reason": "bad request"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="bad request"):
            open_meteo.daily_weather(_POINT, days=2, client=client)


def test_empty_daily_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"daily": {"time": []}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="未返回可用日数据"):
            open_meteo.daily_weather(_POINT, days=2, client=client)
