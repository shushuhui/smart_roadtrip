"""费用估算测试。"""

from __future__ import annotations

from datetime import date

from smart_roadtrip import config
from smart_roadtrip.orchestrator.main_agent import _estimate_cost
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    ChargingStop,
    EnergyType,
    RouteOption,
    Traveler,
    TripRequest,
    VehicleInfo,
)


def _state(
    energy_type: EnergyType,
    charging_stops: list[ChargingStop] | None = None,
) -> dict:
    request = TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=2,
        start_date=date(2026, 11, 1),
        travelers=[Traveler(name="我", is_driver=True)],
        vehicle=VehicleInfo(energy_type=energy_type),
    )
    results = {}
    if charging_stops is not None:
        results[AgentName.ENERGY.value] = AgentResult(
            agent=AgentName.ENERGY, charging_stops=charging_stops
        )
    return {"request": request, "agent_results": results}


def _route(*, placeholder: bool = False) -> RouteOption:
    return RouteOption(
        name="测试路线",
        distance_km=1000,
        duration_min=600,
        tolls_cny=200,
        is_placeholder=placeholder,
    )


def _configure_prices(monkeypatch) -> None:
    monkeypatch.setattr(config, "FUEL_PRICE_CNY_PER_L", 7.5)
    monkeypatch.setattr(config, "FUEL_CONSUMPTION_L_PER_100KM", 8.0)
    monkeypatch.setattr(config, "ELECTRICITY_PRICE_CNY_PER_KWH", 1.2)
    monkeypatch.setattr(config, "EV_CONSUMPTION_KWH_PER_100KM", 16.0)
    monkeypatch.setattr(config, "PHEV_FUEL_SHARE", 0.5)


def test_ice_cost(monkeypatch) -> None:
    _configure_prices(monkeypatch)

    cost = _estimate_cost(_state(EnergyType.ICE), _route())

    assert cost.energy_cost_cny == 600
    assert cost.tolls_cny == 200
    assert cost.total_cny == 800
    assert cost.is_placeholder is False


def test_ev_cost_uses_charging_price(monkeypatch) -> None:
    _configure_prices(monkeypatch)
    stop = ChargingStop(
        day_index=2,
        name="充电站",
        arrival_soc=0.2,
        target_soc=0.8,
        charge_minutes=30,
        price_per_kwh=1.5,
    )

    cost = _estimate_cost(_state(EnergyType.EV, [stop]), _route())

    assert cost.energy_cost_cny == 240
    assert cost.tolls_cny == 200
    assert cost.total_cny == 440


def test_ev_cost_falls_back_to_default_price(monkeypatch) -> None:
    _configure_prices(monkeypatch)

    cost = _estimate_cost(_state(EnergyType.EV), _route())

    assert cost.energy_cost_cny == 192
    assert cost.total_cny == 392


def test_phev_blends_fuel_and_electricity(monkeypatch) -> None:
    _configure_prices(monkeypatch)

    cost = _estimate_cost(_state(EnergyType.PHEV), _route())

    assert cost.energy_cost_cny == 396
    assert cost.total_cny == 596


def test_placeholder_route_marks_estimate(monkeypatch) -> None:
    _configure_prices(monkeypatch)

    cost = _estimate_cost(
        _state(EnergyType.ICE), _route(placeholder=True)
    )

    assert cost.is_placeholder is True
    assert cost.tolls_cny == 0
    assert any("占位" in item for item in cost.assumptions)
