"""导出测试：Markdown 拼接、HTML 渲染与 GPX 结构。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

import pytest

from smart_roadtrip.orchestrator.export import (
    ExportError,
    GPX_NAMESPACE,
    render_gpx,
    render_html,
    render_markdown_export,
    render_pdf,
)
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    ChargingStop,
    GeoPoint,
    Itinerary,
    RescueResource,
    RouteOption,
    Traveler,
    TripRequest,
)


def _state() -> dict:
    request = TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=3,
        start_date=date(2026, 11, 1),
        travelers=[Traveler(name="我", is_driver=True)],
    )
    route = AgentResult(
        agent=AgentName.ROUTE,
        is_placeholder=False,
        route_options=[
            RouteOption(
                name="高德推荐",
                distance_km=900,
                duration_min=720,
                tolls_cny=200,
                polyline=[
                    GeoPoint(lng=104.0, lat=30.5),
                    GeoPoint(lng=100.3, lat=28.4),
                ],
            )
        ],
    )
    energy = AgentResult(
        agent=AgentName.ENERGY,
        charging_stops=[
            ChargingStop(
                day_index=3,
                name="稻城充电站",
                location=GeoPoint(lng=100.31, lat=28.44),
                arrival_soc=0.2,
                target_soc=0.8,
                charge_minutes=30,
                source="amap_poi",
            )
        ],
    )
    safety = AgentResult(
        agent=AgentName.SAFETY,
        rescue_resources=[
            RescueResource(
                name="稻城县人民医院",
                category="医院",
                location=GeoPoint(lng=100.32, lat=28.45),
                address="稻城县",
                phone="0836-120",
            )
        ],
    )
    return {
        "request": request,
        "itinerary": Itinerary(title="成都 → 稻城亚丁 3 日自驾行程"),
        "report_markdown": "# 路书\n\n## 每日安排\n\n- 第 1 天",
        "agent_results": {
            AgentName.ROUTE.value: route,
            AgentName.ENERGY.value: energy,
            AgentName.SAFETY.value: safety,
        },
        "status": "completed",
    }


def test_markdown_export_appends_journal() -> None:
    markdown = render_markdown_export(_state(), "# 游记\n\n正文")

    assert "## 每日安排" in markdown
    assert "---" in markdown
    assert "游记" in markdown


def test_html_export_renders_markdown_and_escapes_html() -> None:
    state = _state()
    html = render_html(state)
    assert html.startswith("<!DOCTYPE html>")
    assert "成都 → 稻城亚丁" in html
    assert "每日安排" in html

    state["report_markdown"] = "# 路书\n\n<script>alert(1)</script>"
    escaped = render_html(state)
    assert "&lt;script&gt;" in escaped
    assert "<script>alert(1)</script>" not in escaped


def test_html_export_includes_journal() -> None:
    html = render_html(_state(), "# 游记\n\n正文")
    assert "游记" in html
    assert "正文" in html


def test_gpx_export_contains_track_and_waypoints() -> None:
    xml = render_gpx(_state())
    root = ET.fromstring(xml)
    ns = {"g": GPX_NAMESPACE}

    assert root.tag == f"{{{GPX_NAMESPACE}}}gpx"
    assert len(root.findall(".//g:trkpt", ns)) == 2
    waypoints = root.findall(".//g:wpt", ns)
    assert len(waypoints) == 2
    names = [point.findtext("g:name", namespaces=ns) for point in waypoints]
    assert any("稻城充电站" in name for name in names)
    assert any("稻城县人民医院" in name for name in names)


def test_gpx_without_coordinates_raises() -> None:
    with pytest.raises(ExportError, match="没有可用的路线坐标"):
        render_gpx({"report_markdown": "# 路书", "agent_results": {}})


def test_export_requires_roadbook() -> None:
    with pytest.raises(ExportError, match="路书尚未生成"):
        render_html({"report_markdown": None})


def test_pdf_export_starts_with_pdf_header() -> None:
    pytest.importorskip("reportlab")

    pdf = render_pdf(_state(), "# 游记\n\n正文")

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000
    assert b"%%EOF" in pdf[-20:]


def test_pdf_requires_roadbook() -> None:
    with pytest.raises(ExportError, match="路书尚未生成"):
        render_pdf({"report_markdown": None})
