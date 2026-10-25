"""多 API key 的线程归属隔离测试。"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.api.runtime import get_graph, reset_runtime
from smart_roadtrip.main import app
from smart_roadtrip.orchestrator.main_agent import start_planning
from smart_roadtrip.schemas.trip import Traveler, TripRequest

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_tenancy(monkeypatch):
    reset_runtime()
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEYS", "key-a,key-b")
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0)
    yield
    reset_runtime()


def _payload() -> dict:
    return {
        "trip": {
            "origin": "成都",
            "destination": "稻城亚丁",
            "days": 2,
            "start_date": "2026-11-01",
            "travelers": [{"name": "我", "is_driver": True}],
            "vehicle": {"energy_type": "ev"},
            "preferences": {"interests": ["雪山"]},
        }
    }


def test_thread_is_isolated_by_api_key() -> None:
    created = client.post(
        "/chat", json=_payload(), headers={"X-API-Key": "key-a"}
    )
    assert created.status_code == 200, created.text
    thread_id = created.json()["thread_id"]

    assert (
        client.get(
            f"/trips/{thread_id}", headers={"X-API-Key": "key-a"}
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/trips/{thread_id}", headers={"X-API-Key": "key-b"}
        ).status_code
        == 404
    )
    assert client.get(f"/trips/{thread_id}").status_code == 401

    assert (
        client.get(
            f"/trips/{thread_id}/report", headers={"X-API-Key": "key-b"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/trips/{thread_id}/confirm",
            json={"decision": "approve"},
            headers={"X-API-Key": "key-b"},
        ).status_code
        == 404
    )


def test_auth_disabled_allows_anonymous(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_KEYS", "")

    created = client.post("/chat", json=_payload())
    assert created.status_code == 200
    thread_id = created.json()["thread_id"]

    assert client.get(f"/trips/{thread_id}").status_code == 200


def test_unowned_thread_is_denied_when_auth_enabled() -> None:
    request = TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=2,
        start_date=date(2026, 11, 1),
        travelers=[Traveler(name="我", is_driver=True)],
    )
    thread_id = "legacy-thread-without-owner"
    start_planning(request, thread_id, graph=get_graph())

    response = client.get(
        f"/trips/{thread_id}", headers={"X-API-Key": "key-a"}
    )
    assert response.status_code == 404
