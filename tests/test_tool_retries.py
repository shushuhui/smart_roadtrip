"""工具层 HTTP 重试测试。"""

from __future__ import annotations

import httpx
import pytest

from smart_roadtrip import config
from smart_roadtrip.tools.base import ToolError, get_json


def _enable_retries(monkeypatch) -> None:
    monkeypatch.setattr(config, "TOOL_MAX_RETRIES", 2)
    monkeypatch.setattr(config, "TOOL_RETRY_BACKOFF_SECONDS", 0)


def test_retries_5xx_then_succeeds(monkeypatch) -> None:
    _enable_retries(monkeypatch)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500, json={"error": "temporary"})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert get_json(client, "https://example.com/x") == {"ok": True}

    assert calls["count"] == 2


def test_retries_429_then_succeeds(monkeypatch) -> None:
    _enable_retries(monkeypatch)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert get_json(client, "https://example.com/x") == {"ok": True}

    assert calls["count"] == 2


def test_does_not_retry_4xx(monkeypatch) -> None:
    _enable_retries(monkeypatch)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, json={"error": "bad"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ToolError):
            get_json(client, "https://example.com/x")

    assert calls["count"] == 1


def test_retries_network_error_then_succeeds(monkeypatch) -> None:
    _enable_retries(monkeypatch)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert get_json(client, "https://example.com/x") == {"ok": True}

    assert calls["count"] == 2
