"""行程删除与配额释放测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.api.runtime import get_graph, reset_runtime
from smart_roadtrip.main import app
from smart_roadtrip.orchestrator.main_agent import get_planning_state

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_deletion(monkeypatch):
    reset_runtime()
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEYS", "key-a,key-b")
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(config, "TENANT_MAX_THREADS_PER_DAY", 1)
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
        }
    }


def _create(key: str) -> str:
    response = client.post(
        "/chat", json=_payload(), headers={"X-API-Key": key}
    )
    assert response.status_code == 200, response.text
    return response.json()["thread_id"]


def test_delete_removes_state_listing_and_quota() -> None:
    thread_id = _create("key-a")
    assert (
        client.get("/trips/usage", headers={"X-API-Key": "key-a"}).json()[
            "used_24h"
        ]
        == 1
    )

    deleted = client.delete(
        f"/trips/{thread_id}", headers={"X-API-Key": "key-a"}
    )
    assert deleted.status_code == 204

    assert (
        client.get(
            f"/trips/{thread_id}", headers={"X-API-Key": "key-a"}
        ).status_code
        == 404
    )
    assert client.get("/trips", headers={"X-API-Key": "key-a"}).json() == []
    assert (
        client.get("/trips/usage", headers={"X-API-Key": "key-a"}).json()[
            "used_24h"
        ]
        == 0
    )
    assert get_planning_state(thread_id, graph=get_graph()) == {}

    # 配额已释放，可以再创建。
    assert _create("key-a")


def test_cannot_delete_another_owners_trip() -> None:
    thread_id = _create("key-a")

    denied = client.delete(
        f"/trips/{thread_id}", headers={"X-API-Key": "key-b"}
    )
    assert denied.status_code == 404

    still_there = client.get(
        f"/trips/{thread_id}", headers={"X-API-Key": "key-a"}
    )
    assert still_there.status_code == 200


def test_delete_unknown_thread_returns_404() -> None:
    response = client.delete(
        "/trips/not-a-thread", headers={"X-API-Key": "key-a"}
    )
    assert response.status_code == 404
