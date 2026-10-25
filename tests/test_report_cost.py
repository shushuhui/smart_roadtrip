"""路书费用章节测试。"""

from __future__ import annotations

from datetime import date

from smart_roadtrip.agents.base import AgentContext
from smart_roadtrip.agents.report_agent import ReportAgent
from smart_roadtrip.schemas.trip import (
    CostEstimate,
    Itinerary,
    Traveler,
    TripRequest,
)


def test_report_shows_cost_and_per_person_split() -> None:
    request = TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=2,
        start_date=date(2026, 11, 1),
        travelers=[Traveler(name="我"), Traveler(name="你")],
    )
    itinerary = Itinerary(
        title="测试行程",
        cost_estimate=CostEstimate(
            energy_cost_cny=800,
            tolls_cny=200,
            total_cny=1000,
            assumptions=["不含住宿、餐饮、门票与停车费。"],
        ),
    )

    result = ReportAgent().run(
        AgentContext(request=request, itinerary=itinerary)
    )
    markdown = result.report_markdown or ""

    assert "## 费用估算" in markdown
    assert "合计：约 1000 元" in markdown
    assert "按 2 人均摊：约 500 元/人" in markdown
    assert "不含住宿、餐饮、门票与停车费" in markdown
