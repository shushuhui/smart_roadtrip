"""游记生成测试：模板降级、LLM 路径与编排入口。"""

from __future__ import annotations

from datetime import date

import pytest

from smart_roadtrip.agents import report_agent as report_agent_module
from smart_roadtrip.agents.base import AgentContext
from smart_roadtrip.agents.report_agent import ReportAgent
from smart_roadtrip.orchestrator.journal import JournalError, generate_travelogue
from smart_roadtrip.schemas.trip import (
    Activity,
    DayPlan,
    Itinerary,
    Traveler,
    TravelPreferences,
    TripRequest,
)


def _request() -> TripRequest:
    return TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=2,
        start_date=date(2026, 11, 1),
        travelers=[Traveler(name="我", is_driver=True)],
        preferences=TravelPreferences(interests=["雪山"]),
    )


def _itinerary() -> Itinerary:
    return Itinerary(
        title="成都 → 稻城亚丁 2 日自驾行程",
        summary="测试行程",
        days=[
            DayPlan(
                day_index=1,
                date=date(2026, 11, 1),
                title="抵达稻城亚丁",
                activities=[Activity(title="从成都出发", kind="交通")],
                driving_km=300,
            ),
            DayPlan(
                day_index=2,
                date=date(2026, 11, 2),
                title="返程",
                activities=[Activity(title="返程", kind="交通")],
                driving_km=300,
            ),
        ],
        total_distance_km=600,
        total_drive_hours=10,
    )


def test_template_travelogue_is_deterministic() -> None:
    ctx = AgentContext(request=_request(), itinerary=_itinerary(), upstream={})

    markdown, source = ReportAgent().generate_travelogue(ctx)

    assert source == "template"
    assert "游记（草稿）" in markdown
    assert "## 第 1 天" in markdown
    assert "## 数据来源" in markdown
    assert "尚未包含真实旅行照片" in markdown


def test_travelogue_uses_llm_when_configured(monkeypatch) -> None:
    class FakeResponse:
        content = "## 稻城亚丁游记\n\n这是一段测试游记正文。"

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def invoke(self, messages):
            return FakeResponse()

    monkeypatch.setattr(report_agent_module, "_llm_configured", lambda: True)
    monkeypatch.setattr(report_agent_module, "ChatOpenAI", FakeClient)

    ctx = AgentContext(request=_request(), itinerary=_itinerary(), upstream={})
    markdown, source = ReportAgent().generate_travelogue(ctx)

    assert source == "llm"
    assert "测试游记正文" in markdown


def test_travelogue_falls_back_when_llm_raises(monkeypatch) -> None:
    class BoomClient:
        def __init__(self, **kwargs) -> None:
            pass

        def invoke(self, messages):
            raise RuntimeError("llm down")

    monkeypatch.setattr(report_agent_module, "_llm_configured", lambda: True)
    monkeypatch.setattr(report_agent_module, "ChatOpenAI", BoomClient)

    ctx = AgentContext(request=_request(), itinerary=_itinerary(), upstream={})
    markdown, source = ReportAgent().generate_travelogue(ctx)

    assert source == "template"
    assert "游记（草稿）" in markdown


def test_journal_orchestrator_from_state() -> None:
    state = {
        "request": _request(),
        "itinerary": _itinerary(),
        "round": 2,
        "revision_count": 1,
        "feedback": "",
        "agent_results": {},
    }

    markdown, source = generate_travelogue(state)

    assert source == "template"
    assert "稻城亚丁" in markdown


def test_journal_requires_itinerary() -> None:
    with pytest.raises(JournalError, match="无法写游记"):
        generate_travelogue({"request": _request()})
