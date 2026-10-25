"""当前 owner 的行程列表与用量测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.api.runtime import reset_runtime
from smart_roadtrip.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_listing(monkeypatch):
    reset_runtime()
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEYS", "key-a,key-b")
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(config, "TENANT_MAX_THREADS_PER_DAY", 0)
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


def test_list_trips_only_returns_current_owner() -> None:
    a1 = _create("key-a")
    a2 = _create("key-a")
    b1 = _create("key-b")

    a_list = client.get("/trips", headers={"X-API-Key": "key-a"})
    assert a_list.status_code == 200, a_list.text
    a_threads = {item["thread_id"] for item in a_list.json()}
    assert a_threads == {a1, a2}
    first = a_list.json()[0]
    assert first["destination"] == "稻城亚丁"
    assert first["status"] == "awaiting_confirmation"

    b_list = client.get("/trips", headers={"X-API-Key": "key-b"})
    assert {item["thread_id"] for item in b_list.json()} == {b1}

    assert client.get("/trips").status_code == 401


def test_list_trips_supports_pagination() -> None:
    for _ in range(3):
        _create("key-a")

    first_page = client.get(
        "/trips?limit=2", headers={"X-API-Key": "key-a"}
    ).json()
    second_page = client.get(
        "/trips?limit=2&offset=2", headers={"X-API-Key": "key-a"}
    ).json()

    assert len(first_page) == 2
    assert len(second_page) == 1
    assert not (
        {item["thread_id"] for item in first_page}
        & {item["thread_id"] for item in second_page}
    )


def test_usage_reports_quota(monkeypatch) -> None:
    monkeypatch.setattr(config, "TENANT_MAX_THREADS_PER_DAY", 2)
    _create("key-a")

    usage = client.get("/trips/usage", headers={"X-API-Key": "key-a"})
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["used_24h"] == 1
    assert body["limit"] == 2
    assert body["remaining"] == 1
    assert body["total_threads"] == 1
    assert body["recent_by_status"] == {"awaiting_confirmation": 1}
    assert body["last_created_at"]
    assert body["recent_estimated_cost_cny"] >= 0

    other = client.get("/trips/usage", headers={"X-API-Key": "key-b"})
    assert other.json()["used_24h"] == 0
    assert other.json()["limit"] == 2
    assert other.json()["remaining"] == 2
    assert other.json()["total_threads"] == 0
    assert other.json()["recent_by_status"] == {}

    assert client.get("/trips/usage").status_code == 401
