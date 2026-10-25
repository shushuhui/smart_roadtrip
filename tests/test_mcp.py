"""MCP server/client 集成测试。

只调用 ``tool_status``，不会访问高德/天气/LLM 等外部服务。
"""

from __future__ import annotations

from datetime import date

import pytest

from smart_roadtrip import config
from smart_roadtrip.agents.base import AgentContext
from smart_roadtrip.agents.route_agent import RouteAgent
from smart_roadtrip.schemas.trip import Traveler, TripRequest
from smart_roadtrip.tools import gateway
from smart_roadtrip.tools.base import ToolError
from smart_roadtrip.tools.mcp import client, server


def _request() -> TripRequest:
    return TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=2,
        start_date=date(2026, 11, 1),
        travelers=[Traveler(name="我", is_driver=True)],
    )


def test_tool_status_direct() -> None:
    status = server.tool_status()

    assert set(status) == {
        "amap_configured",
        "caiyun_configured",
        "open_meteo_enabled",
        "rag_enabled",
        "rag_index_available",
        "commercial_charging_configured",
        "commercial_rescue_configured",
    }
    assert isinstance(status["rag_index_available"], bool)


def test_mcp_client_lists_and_calls_tool() -> None:
    tools = client.list_tools()
    names = {tool["name"] for tool in tools}

    assert {
        "tool_status",
        "amap_geocode",
        "amap_route",
        "amap_nearby",
        "weather_forecast",
        "rag_search",
        "charging_nearby",
        "rescue_nearby",
    } <= names

    status = client.call_tool("tool_status")
    assert isinstance(status, dict)
    assert "amap_configured" in status
    assert isinstance(status["rag_index_available"], bool)


def test_gateway_mcp_backend_status(monkeypatch) -> None:
    monkeypatch.setattr(config, "TOOL_BACKEND", "mcp")
    gateway.reset_gateway_cache()
    try:
        status = gateway.tool_status()
        assert isinstance(status, dict)
        assert "amap_configured" in status
        # 测试环境密钥已清空，server 子进程也会收到空 key。
        assert gateway.amap_configured() is False
    finally:
        gateway.reset_gateway_cache()


def test_gateway_mcp_tool_error(monkeypatch) -> None:
    monkeypatch.setattr(config, "TOOL_BACKEND", "mcp")
    gateway.reset_gateway_cache()
    try:
        with pytest.raises(ToolError):
            gateway.amap_route_plan("成都", "康定")
    finally:
        gateway.reset_gateway_cache()


def test_route_agent_mcp_mode_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(config, "TOOL_BACKEND", "mcp")
    gateway.reset_gateway_cache()
    try:
        result = RouteAgent().run(AgentContext(request=_request()))
        assert result.is_placeholder is True
    finally:
        gateway.reset_gateway_cache()


def test_mcp_gateway_concurrent_calls() -> None:
    from concurrent.futures import ThreadPoolExecutor

    from smart_roadtrip.tools.mcp.gateway import get_gateway, reset_gateway

    reset_gateway()
    try:
        mcp_gateway = get_gateway()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _: mcp_gateway.call_tool("tool_status", {}),
                    range(4),
                )
            )
        assert all(isinstance(item, dict) for item in results)
        assert all("amap_configured" in item for item in results)
    finally:
        reset_gateway()


def test_mcp_retry_on_unavailable(monkeypatch) -> None:
    from smart_roadtrip.tools import gateway as tool_gateway
    from smart_roadtrip.tools.mcp import gateway as mcp_gateway_module
    from smart_roadtrip.tools.mcp.gateway import MCPUnavailableError

    calls = {"count": 0}

    class FakeGateway:
        def call_tool(self, name, arguments):
            calls["count"] += 1
            if calls["count"] == 1:
                raise MCPUnavailableError("down")
            return {"ok": True}

    monkeypatch.setattr(
        mcp_gateway_module, "get_gateway", lambda: FakeGateway()
    )
    monkeypatch.setattr(
        mcp_gateway_module, "reset_gateway", lambda: None
    )

    result = tool_gateway._mcp_call("tool_status", {})

    assert result == {"ok": True}
    assert calls["count"] == 2
