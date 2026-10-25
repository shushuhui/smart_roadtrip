"""自然语言需求解析测试：规则路径与 LLM 降级。"""

from __future__ import annotations

from datetime import date

import pytest

from smart_roadtrip import config
from smart_roadtrip.orchestrator import request_parser
from smart_roadtrip.orchestrator.request_parser import (
    RequestParseError,
    parse_request_text,
)
from smart_roadtrip.schemas.trip import (
    EnergyType,
    TravelPace,
    Traveler,
    TripRequest,
)


@pytest.fixture(autouse=True)
def disable_llm(monkeypatch) -> None:
    """默认强制走规则解析，避免测试环境误连真实 LLM。"""

    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")


def test_rule_parser_extracts_full_request() -> None:
    request = parse_request_text(
        "从成都出发，去稻城亚丁玩4天，两个人，开电车，喜欢雪山和摄影，预算8000元",
        today=date(2026, 10, 25),
    )

    assert request.origin == "成都"
    assert request.destination == "稻城亚丁"
    assert request.days == 4
    assert request.start_date == date(2026, 11, 1)
    assert len(request.travelers) == 2
    assert request.travelers[0].is_driver is True
    assert request.vehicle.energy_type == EnergyType.EV
    assert "雪山" in request.preferences.interests
    assert "摄影" in request.preferences.interests
    assert request.budget_cny == 8000


def test_explicit_date_and_chinese_numerals() -> None:
    request = parse_request_text(
        "2026-12-01 从成都到康定三天，开油车",
        today=date(2026, 10, 25),
    )

    assert request.start_date == date(2026, 12, 1)
    assert request.days == 3
    assert request.vehicle.energy_type == EnergyType.ICE


def test_relative_date_and_preferences() -> None:
    request = parse_request_text(
        "明天从成都到康定玩三天，休闲游，喜欢温泉和美食，避开人多的地方",
        today=date(2026, 10, 25),
    )

    assert request.start_date == date(2026, 10, 26)
    assert request.days == 3
    assert request.preferences.pace == TravelPace.RELAXED
    assert "温泉" in request.preferences.interests
    assert "美食" in request.preferences.interests
    assert request.preferences.avoid == ["人多的地方"]


def test_budget_in_wan() -> None:
    request = parse_request_text(
        "从成都到康定玩3天，预算1.5万",
        today=date(2026, 10, 25),
    )
    assert request.budget_cny == 15000


def test_missing_destination_raises() -> None:
    with pytest.raises(RequestParseError, match="目的地"):
        parse_request_text("从成都出发玩几天", today=date(2026, 10, 25))


def test_missing_origin_raises() -> None:
    with pytest.raises(RequestParseError, match="出发地"):
        parse_request_text("去稻城亚丁玩4天", today=date(2026, 10, 25))


def test_llm_path_is_used_when_configured(monkeypatch) -> None:
    expected = TripRequest(
        origin="成都",
        destination="康定",
        days=2,
        start_date=date(2026, 10, 26),
        travelers=[Traveler(name="我")],
    )
    monkeypatch.setattr(request_parser, "is_llm_configured", lambda: True)
    monkeypatch.setattr(request_parser, "_parse_with_llm", lambda text, today: expected)

    assert parse_request_text("随便说点什么", today=date(2026, 10, 25)) is expected


def test_normalize_llm_payload_handles_common_shapes() -> None:
    payload = {
        "origin": {"name": "成都"},
        "destination": {"city": "康定"},
        "days": "三天",
        "start_date": "明天",
        "travelers": 2,
        "preferences": {"pace": "轻松", "interests": "雪山,摄影", "avoid": "人多"},
        "vehicle": {
            "energy_type": "电车",
            "current_soc": "80%",
            "rated_range_km": "500公里",
            "seats": "5座",
        },
        "budget_cny": "8000元",
    }

    normalized = request_parser._normalize_llm_payload(payload, date(2026, 10, 25))
    request = TripRequest.model_validate(normalized)

    assert request.origin == "成都"
    assert request.destination == "康定"
    assert request.days == 3
    assert request.start_date == date(2026, 10, 26)
    assert len(request.travelers) == 2
    assert request.preferences.pace == TravelPace.RELAXED
    assert request.preferences.interests == ["雪山", "摄影"]
    assert request.preferences.avoid == ["人多"]
    assert request.vehicle.energy_type == EnergyType.EV
    assert request.vehicle.current_soc == 0.8
    assert request.vehicle.rated_range_km == 500
    assert request.vehicle.seats == 5
    assert request.budget_cny == 8000


def test_parse_with_llm_extracts_fenced_json(monkeypatch) -> None:
    class FakeResponse:
        content = (
            "```json\n"
            '{"origin": "成都", "destination": "康定", "days": 3, '
            '"start_date": "2026-10-26", '
            '"travelers": [{"name": "我", "is_driver": true}], '
            '"vehicle": {"energy_type": "ev", "current_soc": 0.8}, '
            '"preferences": {"interests": ["雪山"]}}\n'
            "```"
        )

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def invoke(self, messages):
            assert any("TripRequest" in getattr(message, "content", "") for message in messages)
            return FakeResponse()

    monkeypatch.setattr(request_parser, "ChatOpenAI", FakeClient)
    monkeypatch.setattr(request_parser, "is_llm_configured", lambda: True)

    request = parse_request_text("从成都到康定玩3天", today=date(2026, 10, 25))

    assert request.origin == "成都"
    assert request.destination == "康定"
    assert request.days == 3
    assert request.vehicle.energy_type == EnergyType.EV
    assert request.preferences.interests == ["雪山"]


def test_llm_failure_falls_back_to_rules(monkeypatch) -> None:
    def boom(text: str, today: date) -> TripRequest:
        raise RuntimeError("llm down")

    monkeypatch.setattr(request_parser, "is_llm_configured", lambda: True)
    monkeypatch.setattr(request_parser, "_parse_with_llm", boom)

    request = parse_request_text(
        "从成都到康定玩3天",
        today=date(2026, 10, 25),
    )
    assert request.origin == "成都"
    assert request.destination == "康定"
    assert request.days == 3
