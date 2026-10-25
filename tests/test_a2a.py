"""A2A 协议与编排层任务信封测试。"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from smart_roadtrip.orchestrator.main_agent import (
    build_graph,
    fanout_router,
    select_agents,
)
from smart_roadtrip.protocols.a2a import (
    A2AMessage,
    A2AResponse,
    A2AStatus,
    new_result,
    new_task,
)
from smart_roadtrip.schemas.trip import AgentName, Traveler, TripRequest


def _request() -> TripRequest:
    return TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=2,
        start_date=date(2026, 11, 1),
        travelers=[Traveler(name="我", is_driver=True)],
    )


def test_task_and_result_correlation() -> None:
    message = new_task(
        sender=AgentName.ORCHESTRATOR,
        recipient=AgentName.ROUTE,
        task="route",
        payload={"round": 1},
    )
    result = new_result(
        message,
        sender=AgentName.ROUTE,
        status=A2AStatus.OK,
        result={"summary": "ok"},
    )

    assert message.correlation_id
    assert result.correlation_id == message.correlation_id
    assert result.recipient == AgentName.ORCHESTRATOR
    assert result.sender == AgentName.ROUTE


def test_fanout_router_builds_a2a_messages() -> None:
    request = _request()
    state = {
        "request": request,
        "tasks": select_agents(request),
        "round": 1,
        "revision_count": 0,
        "feedback": "",
    }

    sends = fanout_router(state)

    assert len(sends) == len(state["tasks"])
    for send in sends:
        message = send.arg["message"]
        assert isinstance(message, A2AMessage)
        assert message.sender == AgentName.ORCHESTRATOR
        assert message.recipient.value == send.node


def test_graph_records_a2a_responses() -> None:
    graph = build_graph()
    config = {"configurable": {"thread_id": f"a2a-{uuid4().hex}"}}

    state = graph.invoke({"request": _request()}, config)
    responses = [
        item
        if isinstance(item, A2AResponse)
        else A2AResponse.model_validate(item)
        for item in state.get("a2a_responses", [])
    ]

    # ICE 请求扇出 route/weather/knowledge/group 四个 Agent。
    assert len(responses) == 4
    assert {item.sender for item in responses} == {
        AgentName.ROUTE,
        AgentName.WEATHER,
        AgentName.KNOWLEDGE,
        AgentName.GROUP,
    }
    assert all(item.status == A2AStatus.OK for item in responses)
    assert all(item.recipient == AgentName.ORCHESTRATOR for item in responses)
