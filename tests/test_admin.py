"""管理员跨租户用量汇总测试。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.api.runtime import reset_runtime
from smart_roadtrip.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_admin(monkeypatch):
    reset_runtime()
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEYS", "key-a,key-b")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "admin-secret")
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


def _create(key: str) -> None:
    response = client.post(
        "/chat", json=_payload(), headers={"X-API-Key": key}
    )
    assert response.status_code == 200, response.text


def test_admin_usage_requires_key() -> None:
    assert client.get("/admin/usage").status_code == 401
    assert (
        client.get(
            "/admin/usage", headers={"X-Admin-Key": "wrong"}
        ).status_code
        == 401
    )


def test_admin_usage_aggregates_tenants() -> None:
    _create("key-a")
    _create("key-a")
    _create("key-b")

    response = client.get(
        "/admin/usage", headers={"X-Admin-Key": "admin-secret"}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_tenants"] == 2
    assert body["total_threads"] == 3
    assert len(body["tenants"]) == 2
    assert body["tenants"][0]["total_threads"] == 2
    assert body["tenants"][1]["total_threads"] == 1
    assert all(len(item["owner"]) == 24 for item in body["tenants"])
    assert all(
        "awaiting_confirmation" in item["by_status"]
        for item in body["tenants"]
    )
    assert all(item["estimated_cost_cny"] >= 0 for item in body["tenants"])
    assert all(item["last_created_at"] for item in body["tenants"])
    assert "thread_id" not in json.dumps(body)


def test_admin_endpoint_disabled_without_key(monkeypatch) -> None:
    monkeypatch.setattr(config, "ADMIN_API_KEY", "")

    response = client.get(
        "/admin/usage", headers={"X-Admin-Key": "anything"}
    )
    assert response.status_code == 403
