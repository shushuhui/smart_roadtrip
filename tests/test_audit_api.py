"""租户审计查询接口测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.api.runtime import reset_runtime
from smart_roadtrip.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_audit(monkeypatch):
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


def _acks(state: dict) -> list[str]:
    return [
        alert["alert_id"]
        for alert in state["pending_confirmation"]["safety_alerts"]
        if alert["requires_user_confirmation"]
    ]


def test_audit_events_are_scoped_to_owner() -> None:
    created = client.post(
        "/chat", json=_payload(), headers={"X-API-Key": "key-a"}
    )
    assert created.status_code == 200, created.text
    thread_id = created.json()["thread_id"]

    approved = client.post(
        f"/trips/{thread_id}/confirm",
        json={
            "decision": "approve",
            "acknowledged_alert_ids": _acks(created.json()["state"]),
        },
        headers={"X-API-Key": "key-a"},
    )
    assert approved.status_code == 200, approved.text

    exported = client.get(
        f"/trips/{thread_id}/export?format=md",
        headers={"X-API-Key": "key-a"},
    )
    assert exported.status_code == 200

    audit = client.get("/trips/audit", headers={"X-API-Key": "key-a"})
    assert audit.status_code == 200, audit.text
    events = {item["event"] for item in audit.json()}
    assert {"thread_created", "thread_confirmed", "trip_exported"} <= events

    other = client.get("/trips/audit", headers={"X-API-Key": "key-b"})
    assert other.status_code == 200
    assert other.json() == []

    assert client.get("/trips/audit").status_code == 401


def test_audit_supports_pagination() -> None:
    for _ in range(3):
        client.post("/chat", json=_payload(), headers={"X-API-Key": "key-a"})

    first = client.get(
        "/trips/audit?limit=2", headers={"X-API-Key": "key-a"}
    ).json()
    second = client.get(
        "/trips/audit?limit=2&offset=2", headers={"X-API-Key": "key-a"}
    ).json()

    assert len(first) == 2
    assert len(second) == 1


def test_audit_persists_with_sqlite(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        config, "CHECKPOINT_DB_PATH", str(tmp_path / "audit.sqlite")
    )
    original_backend = config.CHECKPOINTER_BACKEND
    config.CHECKPOINTER_BACKEND = "sqlite"

    try:
        reset_runtime()
        created = client.post(
            "/chat", json=_payload(), headers={"X-API-Key": "key-a"}
        )
        assert created.status_code == 200, created.text

        # 模拟重启：内存状态清空，但 SQLite 中的 audit_events 仍在。
        reset_runtime()

        audit = client.get("/trips/audit", headers={"X-API-Key": "key-a"})
        assert audit.status_code == 200, audit.text
        assert any(
            item["event"] == "thread_created" for item in audit.json()
        )
    finally:
        config.CHECKPOINTER_BACKEND = original_backend
        reset_runtime()
