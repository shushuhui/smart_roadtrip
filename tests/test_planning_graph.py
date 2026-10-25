"""MainAgent 图骨架的端到端测试。

不需要任何 API key：所有子 Agent 都是确定性占位实现。
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from langgraph.types import Command

from smart_roadtrip.agents.base import failed_result
from smart_roadtrip.agents.route_agent import RouteAgent
from smart_roadtrip.orchestrator.main_agent import build_graph, rollback_node
from smart_roadtrip.orchestrator.state import merge_agent_results
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    EnergyType,
    Itinerary,
    PendingConfirmation,
    TripRequest,
    TripStatus,
    TripVersion,
    TravelPreferences,
    Traveler,
    VehicleInfo,
)


def make_request(energy_type: EnergyType = EnergyType.EV) -> TripRequest:
    return TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=3,
        start_date=date(2026, 10, 26),
        travelers=[
            Traveler(name="小张", is_driver=True, preferences=["雪山"]),
            Traveler(name="小李", preferences=["摄影"]),
        ],
        vehicle=VehicleInfo(
            energy_type=energy_type,
            rated_range_km=500,
            current_soc=0.8,
        ),
        preferences=TravelPreferences(interests=["雪山", "摄影"]),
        budget_cny=6000,
    )


def _config() -> dict:
    return {"configurable": {"thread_id": f"test-{uuid4().hex}"}}


def _ack_ids(result: dict) -> list[str]:
    pending = result["pending_confirmation"]
    if not isinstance(pending, PendingConfirmation):
        pending = PendingConfirmation.model_validate(pending)
    return [
        alert.alert_id
        for alert in pending.safety_alerts
        if alert.requires_user_confirmation
    ]


def _result_map(state: dict) -> dict[str, AgentResult]:
    return {
        name: value if isinstance(value, AgentResult) else AgentResult.model_validate(value)
        for name, value in (state.get("agent_results") or {}).items()
    }


def test_initial_plan_waits_for_confirmation() -> None:
    graph = build_graph()
    config = _config()

    state = graph.invoke({"request": make_request()}, config)

    assert "__interrupt__" in state
    assert state["status"] == TripStatus.AWAITING_CONFIRMATION
    assert state["pending_confirmation"] is not None
    assert len(state["versions"]) == 1
    assert state["current_version_id"] == state["versions"][0].version_id

    results = _result_map(state)
    assert set(results) == {"route", "weather", "energy", "knowledge", "group", "safety"}
    assert all(result.is_placeholder for result in results.values())


def test_approval_completes_and_generates_report() -> None:
    graph = build_graph()
    config = _config()

    interrupted = graph.invoke({"request": make_request()}, config)
    state = graph.invoke(
        Command(
            resume={
                "decision": "approve",
                "acknowledged_alert_ids": _ack_ids(interrupted),
            }
        ),
        config,
    )

    assert state["status"] == TripStatus.COMPLETED
    assert state["confirmed_version_id"] == state["current_version_id"]
    assert state["pending_confirmation"] is None
    assert state["report_markdown"]
    assert "占位" in state["report_markdown"]
    assert "## 每日安排" in state["report_markdown"]


def test_ice_vehicle_skips_energy_agent() -> None:
    graph = build_graph()
    state = graph.invoke({"request": make_request(EnergyType.ICE)}, _config())

    assert "energy" not in state["tasks"]
    assert "energy" not in _result_map(state)


def test_reject_then_approve_confirms_next_version() -> None:
    graph = build_graph()
    config = _config()

    first = graph.invoke({"request": make_request()}, config)
    first_version_id = first["current_version_id"]

    second = graph.invoke(
        Command(resume={"decision": "reject", "feedback": "想少开一点车"}),
        config,
    )
    assert "__interrupt__" in second
    assert second["round"] == 2
    assert second["revision_count"] == 1
    assert len(second["versions"]) == 2
    assert second["current_version_id"] != first_version_id

    final = graph.invoke(
        Command(
            resume={
                "decision": "approve",
                "acknowledged_alert_ids": _ack_ids(second),
            }
        ),
        config,
    )
    assert final["status"] == TripStatus.COMPLETED
    assert final["confirmed_version_id"] == second["current_version_id"]


def test_revision_limit_rolls_back_without_confirmed_version() -> None:
    graph = build_graph(max_revisions=1)
    config = _config()

    graph.invoke({"request": make_request()}, config)
    still_interrupted = graph.invoke(
        Command(resume={"decision": "reject", "feedback": "再改一版"}),
        config,
    )
    assert "__interrupt__" in still_interrupted

    final = graph.invoke(
        Command(resume={"decision": "reject", "feedback": "还是不行"}),
        config,
    )
    assert final["status"] == TripStatus.FAILED
    assert final["itinerary"] is None
    assert any("无可回退" in error for error in final["errors"])


def test_approval_without_acknowledging_alerts_is_rejected() -> None:
    graph = build_graph()
    config = _config()

    graph.invoke({"request": make_request()}, config)
    state = graph.invoke(
        Command(resume={"decision": "approve", "acknowledged_alert_ids": []}),
        config,
    )

    assert "__interrupt__" in state
    assert state["revision_count"] == 1
    assert "安全提示未确认" in state["feedback"]


def test_route_failure_uses_fallback_itinerary(monkeypatch) -> None:
    def fail_route(self, ctx):
        return failed_result(AgentName.ROUTE, "模拟路线服务不可用", round=ctx.round)

    monkeypatch.setattr(RouteAgent, "_run", fail_route)

    graph = build_graph()
    state = graph.invoke({"request": make_request()}, _config())

    assert "__interrupt__" in state
    assert state["itinerary"] is not None
    assert state["itinerary"].is_placeholder is True
    assert any("route Agent 执行失败" in error for error in state["errors"])


def test_merge_agent_results_resets_on_none() -> None:
    route_result = AgentResult(agent=AgentName.ROUTE)

    assert merge_agent_results({AgentName.ROUTE.value: route_result}, None) == {}

    merged = merge_agent_results(
        {AgentName.ROUTE.value: route_result},
        {AgentName.WEATHER.value: AgentResult(agent=AgentName.WEATHER)},
    )
    assert set(merged) == {AgentName.ROUTE.value, AgentName.WEATHER.value}


def test_rollback_restores_confirmed_version_as_copy() -> None:
    itinerary = Itinerary(title="已确认行程", summary="快照")
    version = TripVersion(number=1, reason="初始方案", itinerary=itinerary)

    update = rollback_node(
        {"versions": [version], "confirmed_version_id": version.version_id}
    )

    assert update["status"] == TripStatus.ROLLED_BACK
    assert update["itinerary"].title == "已确认行程"
    assert update["itinerary"] is not itinerary


def test_rollback_without_confirmed_version_fails() -> None:
    version = TripVersion(
        number=1,
        reason="初始方案",
        itinerary=Itinerary(title="未确认行程"),
    )

    update = rollback_node(
        {"versions": [version], "confirmed_version_id": None}
    )

    assert update["status"] == TripStatus.FAILED
    assert update["itinerary"] is None
    assert any("无可回退" in error for error in update["errors"])
