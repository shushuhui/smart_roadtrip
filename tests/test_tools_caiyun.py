"""彩云天气工具测试：使用 httpx.MockTransport，不访问真实网络。"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import GeoPoint
from smart_roadtrip.tools import caiyun
from smart_roadtrip.tools.base import ToolError, ToolUnavailableError

_POINT = GeoPoint(name="稻城亚丁", lng=100.297, lat=28.435)


def test_daily_weather_parses_forecast(monkeypatch) -> None:
    monkeypatch.setattr(config, "CAIYUN_API_KEY", "test-key")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {
                    "daily": {
                        "status": "ok",
                        "skycon": [
                            {"date": "2026-11-01", "value": "CLEAR_DAY"},
                            {"date": "2026-11-02", "value": "LIGHT_RAIN"},
                        ],
                        "temperature": [
                            {"date": "2026-11-01", "min": 5, "max": 18},
                            {"date": "2026-11-02", "min": 6, "max": 15},
                        ],
                    }
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        forecast = caiyun.daily_weather(_POINT, days=2, client=client)

    assert requests
    assert "test-key" in str(requests[0].url)
    assert len(forecast) == 2
    assert forecast[0].date == date(2026, 11, 1)
    assert forecast[0].condition == "晴"
    assert forecast[0].temp_low_c == 5
    assert forecast[0].temp_high_c == 18
    assert forecast[0].precipitation_probability == pytest.approx(0.05)
    assert forecast[1].condition == "小雨"
    assert forecast[1].precipitation_probability == pytest.approx(0.7)
    assert forecast[1].warning and "小雨" in forecast[1].warning


def test_missing_key_raises_unavailable() -> None:
    with pytest.raises(ToolUnavailableError, match="CAIYUN_API_KEY"):
        caiyun.daily_weather(_POINT)


def test_caiyun_error_status_raises(monkeypatch) -> None:
    monkeypatch.setattr(config, "CAIYUN_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "failed", "message": "invalid token"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="failed"):
            caiyun.daily_weather(_POINT, days=2, client=client)


def test_empty_forecast_raises(monkeypatch) -> None:
    monkeypatch.setattr(config, "CAIYUN_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {"daily": {"status": "ok", "skycon": [], "temperature": []}},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError, match="未返回可用日数据"):
            caiyun.daily_weather(_POINT, days=2, client=client)
