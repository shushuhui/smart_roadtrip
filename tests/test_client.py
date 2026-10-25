"""Python HTTP 客户端测试（httpx.MockTransport）。"""

from __future__ import annotations

import httpx
import pytest

from smart_roadtrip.client import RoadtripAPIError, RoadtripClient


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("X-API-Key") == "secret"
    path = request.url.path

    if request.method == "POST" and path == "/chat":
        return httpx.Response(
            200,
            json={"thread_id": "t1", "awaiting_confirmation": True},
        )
    if request.method == "GET" and path == "/trips":
        return httpx.Response(200, json=[{"thread_id": "t1"}])
    if request.method == "GET" and path == "/trips/t1":
        return httpx.Response(200, json={"thread_id": "t1", "status": "ok"})
    if request.method == "POST" and path == "/trips/t1/confirm":
        return httpx.Response(200, json={"thread_id": "t1", "status": "completed"})
    if request.method == "GET" and path == "/trips/t1/export":
        return httpx.Response(
            200,
            content=b"%PDF-test",
            headers={"Content-Type": "application/pdf"},
        )
    if request.method == "GET" and path == "/trips/usage":
        return httpx.Response(200, json={"used_24h": 1, "limit": 2})
    if request.method == "GET" and path == "/trips/audit":
        return httpx.Response(200, json=[{"event": "thread_created"}])
    if request.method == "DELETE" and path == "/trips/t1":
        return httpx.Response(204)
    if request.method == "GET" and path == "/trips/missing":
        return httpx.Response(404, json={"detail": "未找到该行程线程。"})
    return httpx.Response(404, json={"detail": "not found"})


def test_client_wraps_api() -> None:
    with httpx.Client(transport=httpx.MockTransport(_handler)) as http:
        client = RoadtripClient(
            "http://test",
            api_key="secret",
            client=http,
        )

        assert client.create_plan(message="你好")["thread_id"] == "t1"
        assert client.list_trips()[0]["thread_id"] == "t1"
        assert client.get_trip("t1")["status"] == "ok"
        assert client.confirm("t1")["status"] == "completed"
        assert client.export("t1", export_format="pdf") == b"%PDF-test"
        assert client.usage()["used_24h"] == 1
        assert client.audit()[0]["event"] == "thread_created"
        assert client.delete("t1") is None


def test_client_raises_api_error() -> None:
    with httpx.Client(transport=httpx.MockTransport(_handler)) as http:
        client = RoadtripClient("http://test", api_key="secret", client=http)
        with pytest.raises(RoadtripAPIError) as exc:
            client.get_trip("missing")
        assert exc.value.status_code == 404
        assert "未找到" in str(exc.value.detail)


def test_create_plan_requires_exactly_one_source() -> None:
    client = RoadtripClient("http://test")
    with pytest.raises(ValueError):
        client.create_plan()
    with pytest.raises(ValueError):
        client.create_plan(trip={}, message="hi")
