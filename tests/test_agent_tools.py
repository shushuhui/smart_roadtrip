"""Agent 与工具层集成测试：真实路径使用 monkeypatch，降级路径验证占位输出。"""

from __future__ import annotations

from datetime import date

from smart_roadtrip.agents import energy_agent as energy_agent_module
from smart_roadtrip.agents import route_agent as route_agent_module
from smart_roadtrip.agents import safety_agent as safety_agent_module
from smart_roadtrip.agents import weather_agent as weather_agent_module
from smart_roadtrip.agents.base import AgentContext
from smart_roadtrip.agents.energy_agent import EnergyAgent
from smart_roadtrip.agents.report_agent import ReportAgent
from smart_roadtrip.agents.route_agent import RouteAgent
from smart_roadtrip.agents.safety_agent import SafetyAgent
from smart_roadtrip.agents.weather_agent import WeatherAgent
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    DrivingLoad,
    EnergyType,
    GeoPoint,
    Itinerary,
    RescueResource,
    Traveler,
    TripRequest,
    VehicleInfo,
)
from smart_roadtrip.tools.amap import AmapPoi, AmapRoute
from smart_roadtrip.tools.base import ToolError
from smart_roadtrip.tools.charging import ChargingStation
from smart_roadtrip.tools.weather_common import DailyForecast


def _request(energy_type: EnergyType = EnergyType.ICE) -> TripRequest:
    return TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=3,
        start_date=date(2026, 10, 26),
        travelers=[Traveler(name="我", is_driver=True)],
        vehicle=VehicleInfo(energy_type=energy_type),
    )


def test_route_agent_uses_amap(monkeypatch) -> None:
    monkeypatch.setattr(route_agent_module.gateway, "amap_configured", lambda: True)
    monkeypatch.setattr(
        route_agent_module.gateway,
        "amap_route_plan",
        lambda origin, destination: [
            AmapRoute(
                name="高德推荐",
                distance_km=1200.0,
                duration_min=1200,
                tolls_cny=300.0,
                polyline=[
                    GeoPoint(lng=104.0, lat=30.5),
                    GeoPoint(lng=100.3, lat=28.4),
                ],
            )
        ],
    )

    result = RouteAgent().run(AgentContext(request=_request()))

    assert result.is_placeholder is False
    assert len(result.route_options) == 1
    option = result.route_options[0]
    assert option.distance_km == 1200.0
    assert option.duration_min == 1200
    assert option.tolls_cny == 300.0
    assert option.driving_load == DrivingLoad.HIGH
    assert len(option.polyline) == 2


def test_route_agent_falls_back_on_tool_error(monkeypatch) -> None:
    monkeypatch.setattr(route_agent_module.gateway, "amap_configured", lambda: True)

    def boom(*args, **kwargs):
        raise ToolError("amap down")

    monkeypatch.setattr(route_agent_module.gateway, "amap_route_plan", boom)

    result = RouteAgent().run(AgentContext(request=_request()))

    assert result.is_placeholder is True
    assert result.route_options


def test_weather_agent_uses_caiyun(monkeypatch) -> None:
    monkeypatch.setattr(weather_agent_module.gateway, "amap_configured", lambda: True)
    monkeypatch.setattr(weather_agent_module.gateway, "caiyun_configured", lambda: True)
    monkeypatch.setattr(
        weather_agent_module.gateway,
        "amap_geocode",
        lambda address: GeoPoint(name=address, lng=100.3, lat=28.4),
    )
    monkeypatch.setattr(
        weather_agent_module.gateway,
        "caiyun_daily_weather",
        lambda point, days: [
            DailyForecast(
                date=date(2026, 10, 26),
                condition="晴",
                temp_low_c=5,
                temp_high_c=18,
                precipitation_probability=0.05,
            )
        ],
    )

    result = WeatherAgent().run(AgentContext(request=_request()))

    assert result.is_placeholder is False
    assert len(result.weather_windows) == 1
    assert result.weather_windows[0].day_index == 1
    assert result.weather_windows[0].condition == "晴"


def test_weather_agent_falls_back_on_tool_error(monkeypatch) -> None:
    monkeypatch.setattr(weather_agent_module.gateway, "amap_configured", lambda: True)
    monkeypatch.setattr(weather_agent_module.gateway, "caiyun_configured", lambda: True)

    def boom(*args, **kwargs):
        raise ToolError("weather down")

    monkeypatch.setattr(weather_agent_module.gateway, "amap_geocode", boom)

    result = WeatherAgent().run(AgentContext(request=_request()))

    assert result.is_placeholder is True
    assert result.weather_windows


def test_safety_note_reflects_real_sources() -> None:
    upstream = {
        AgentName.ROUTE.value: AgentResult(
            agent=AgentName.ROUTE, is_placeholder=False
        ),
        AgentName.WEATHER.value: AgentResult(
            agent=AgentName.WEATHER, is_placeholder=False
        ),
        AgentName.ENERGY.value: AgentResult(
            agent=AgentName.ENERGY, is_placeholder=True
        ),
    }

    result = SafetyAgent().run(AgentContext(request=_request(), upstream=upstream))
    note = result.safety_alerts[0].message

    assert "路线、天气" in note
    assert "补能" in note


def test_report_note_reflects_real_sources() -> None:
    upstream = {
        AgentName.ROUTE.value: AgentResult(
            agent=AgentName.ROUTE, is_placeholder=False
        ),
        AgentName.WEATHER.value: AgentResult(
            agent=AgentName.WEATHER, is_placeholder=False
        ),
    }
    context = AgentContext(
        request=_request(),
        itinerary=Itinerary(title="测试行程"),
        upstream=upstream,
    )

    result = ReportAgent().run(context)

    assert result.report_markdown
    assert "路线、天气数据来自外部服务" in result.report_markdown


def test_weather_agent_uses_open_meteo_when_caiyun_missing(monkeypatch) -> None:
    monkeypatch.setattr(weather_agent_module.gateway, "amap_configured", lambda: True)
    monkeypatch.setattr(weather_agent_module.gateway, "caiyun_configured", lambda: False)
    monkeypatch.setattr(
        weather_agent_module.gateway,
        "amap_geocode",
        lambda address: GeoPoint(name=address, lng=100.3, lat=28.4),
    )
    forecast = [
        DailyForecast(
            date=date(2026, 10, 26),
            condition="晴",
            temp_low_c=5,
            temp_high_c=18,
            precipitation_probability=0.10,
        ),
        DailyForecast(
            date=date(2026, 10, 27),
            condition="多云",
            temp_low_c=4,
            temp_high_c=16,
            precipitation_probability=0.20,
        ),
        DailyForecast(
            date=date(2026, 10, 28),
            condition="小雨",
            temp_low_c=3,
            temp_high_c=12,
            precipitation_probability=0.70,
        ),
    ]
    monkeypatch.setattr(
        weather_agent_module.gateway,
        "open_meteo_daily_weather",
        lambda point, days: forecast,
    )

    result = WeatherAgent().run(AgentContext(request=_request()))

    assert result.is_placeholder is False
    assert len(result.weather_windows) == 3
    assert result.data_sources == ["amap_geocode", "open_meteo"]
    assert "Open-Meteo" in result.summary


def test_weather_agent_prefers_open_meteo_when_it_covers_more_days(monkeypatch) -> None:
    monkeypatch.setattr(weather_agent_module.gateway, "amap_configured", lambda: True)
    monkeypatch.setattr(weather_agent_module.gateway, "caiyun_configured", lambda: True)
    monkeypatch.setattr(
        weather_agent_module.gateway,
        "amap_geocode",
        lambda address: GeoPoint(name=address, lng=100.3, lat=28.4),
    )
    monkeypatch.setattr(
        weather_agent_module.gateway,
        "caiyun_daily_weather",
        lambda point, days: [
            DailyForecast(
                date=date(2026, 10, 26),
                condition="晴",
                temp_low_c=5,
                temp_high_c=18,
                precipitation_probability=0.10,
            )
        ],
    )
    monkeypatch.setattr(
        weather_agent_module.gateway,
        "open_meteo_daily_weather",
        lambda point, days: [
            DailyForecast(
                date=date(2026, 10, 26 + offset),
                condition="晴",
                temp_low_c=5,
                temp_high_c=18,
                precipitation_probability=0.10,
            )
            for offset in range(3)
        ],
    )

    result = WeatherAgent().run(AgentContext(request=_request()))

    assert len(result.weather_windows) == 3
    assert result.data_sources == ["amap_geocode", "open_meteo"]


def test_energy_agent_uses_amap_pois(monkeypatch) -> None:
    monkeypatch.setattr(energy_agent_module.gateway, "amap_configured", lambda: True)
    monkeypatch.setattr(
        energy_agent_module.gateway,
        "amap_geocode",
        lambda address: GeoPoint(name=address, lng=100.3, lat=28.4),
    )
    monkeypatch.setattr(
        energy_agent_module.gateway,
        "amap_search_charging_stations",
        lambda point, radius_m, limit: [
            AmapPoi(
                name="稻城县城充电站",
                address="稻城县",
                location=GeoPoint(lng=100.31, lat=28.44),
                distance_m=1200,
            )
        ],
    )

    result = EnergyAgent().run(AgentContext(request=_request(EnergyType.EV)))

    assert result.is_placeholder is False
    assert result.data_sources == ["amap_poi"]
    assert len(result.charging_stops) == 1
    stop = result.charging_stops[0]
    assert stop.name == "稻城县城充电站"
    assert stop.source == "amap_poi"
    assert stop.queue_probability is None
    assert stop.price_per_kwh is None
    assert "高德 POI" in stop.notes


def test_energy_agent_falls_back_without_pois(monkeypatch) -> None:
    monkeypatch.setattr(energy_agent_module.gateway, "amap_configured", lambda: True)
    monkeypatch.setattr(
        energy_agent_module.gateway,
        "amap_geocode",
        lambda address: GeoPoint(name=address, lng=100.3, lat=28.4),
    )
    monkeypatch.setattr(
        energy_agent_module.gateway,
        "amap_search_charging_stations",
        lambda point, radius_m, limit: [],
    )

    result = EnergyAgent().run(AgentContext(request=_request(EnergyType.EV)))

    assert result.is_placeholder is True
    assert result.charging_stops
    assert all(stop.source == "placeholder" for stop in result.charging_stops)


def test_safety_agent_includes_amap_rescue_resources(monkeypatch) -> None:
    point = GeoPoint(name="稻城亚丁", lng=100.3, lat=28.4)
    monkeypatch.setattr(safety_agent_module.gateway, "amap_configured", lambda: True)
    monkeypatch.setattr(safety_agent_module.gateway, "amap_geocode", lambda address: point)
    monkeypatch.setattr(
        safety_agent_module.gateway,
        "amap_search_hospitals",
        lambda point, limit: [
            AmapPoi(
                name="稻城县人民医院",
                address="稻城县",
                location=point,
                tel="0836-120",
            )
        ],
    )
    monkeypatch.setattr(
        safety_agent_module.gateway,
        "amap_search_pharmacies",
        lambda point, limit: [
            AmapPoi(name="稻城药店", address="稻城县", location=point)
        ],
    )
    monkeypatch.setattr(
        safety_agent_module.gateway,
        "amap_search_repair_shops",
        lambda point, limit: [
            AmapPoi(name="稻城汽修", address="稻城县", location=point)
        ],
    )

    result = SafetyAgent().run(AgentContext(request=_request()))

    assert len(result.rescue_resources) == 3
    assert {resource.category for resource in result.rescue_resources} == {
        "医院",
        "药店",
        "汽车维修",
    }
    assert result.data_sources == ["amap_poi"]
    assert "外部数据源" in result.safety_alerts[0].message
    assert "高德 POI" in result.summary


def test_energy_agent_uses_commercial_charging(monkeypatch) -> None:
    monkeypatch.setattr(
        energy_agent_module.gateway,
        "commercial_charging_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        energy_agent_module.gateway, "amap_configured", lambda: True
    )
    monkeypatch.setattr(
        energy_agent_module.gateway,
        "amap_geocode",
        lambda address: GeoPoint(name=address, lng=100.3, lat=28.4),
    )
    monkeypatch.setattr(
        energy_agent_module.gateway,
        "commercial_charging_nearby",
        lambda point, radius_km, limit: [
            ChargingStation(
                name="商业充电站",
                location=GeoPoint(lng=100.31, lat=28.44),
                price_per_kwh=1.25,
                queue_probability=0.3,
                available=4,
                charge_minutes=25,
            )
        ],
    )

    result = EnergyAgent().run(AgentContext(request=_request(EnergyType.EV)))

    assert result.is_placeholder is False
    assert result.data_sources == ["commercial_charging"]
    stop = result.charging_stops[0]
    assert stop.price_per_kwh == 1.25
    assert stop.queue_probability == 0.3
    assert "可用枪数" in stop.notes


def test_safety_agent_uses_commercial_rescue(monkeypatch) -> None:
    point = GeoPoint(name="稻城亚丁", lng=100.3, lat=28.4)
    monkeypatch.setattr(
        safety_agent_module.gateway, "amap_configured", lambda: True
    )
    monkeypatch.setattr(
        safety_agent_module.gateway,
        "commercial_rescue_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        safety_agent_module.gateway, "amap_geocode", lambda address: point
    )
    monkeypatch.setattr(
        safety_agent_module.gateway,
        "commercial_rescue_nearby",
        lambda point, radius_km, limit: [
            RescueResource(
                name="商业医院",
                category="医院",
                location=point,
                source="commercial_rescue",
            )
        ],
    )

    result = SafetyAgent().run(AgentContext(request=_request()))

    assert result.rescue_resources
    assert result.rescue_resources[0].source == "commercial_rescue"
    assert result.data_sources == ["commercial_rescue"]
    assert "商业救援网络" in result.summary


def test_report_includes_rescue_resources_and_sources() -> None:
    rescue = RescueResource(
        name="稻城县人民医院",
        category="医院",
        address="稻城县",
        phone="0836-120",
    )
    upstream = {
        AgentName.SAFETY.value: AgentResult(
            agent=AgentName.SAFETY,
            is_placeholder=True,
            rescue_resources=[rescue],
            data_sources=["amap_poi"],
        ),
        AgentName.WEATHER.value: AgentResult(
            agent=AgentName.WEATHER,
            is_placeholder=False,
            data_sources=["amap_geocode", "open_meteo"],
        ),
    }
    context = AgentContext(
        request=_request(),
        itinerary=Itinerary(title="测试行程"),
        upstream=upstream,
    )

    markdown = ReportAgent().run(context).report_markdown

    assert "## 附近医疗与维修资源" in markdown
    assert "稻城县人民医院" in markdown
    assert "## 数据来源" in markdown
    assert "Open-Meteo" in markdown
    assert "CC BY 4.0" in markdown
