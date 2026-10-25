"""多租户行程创建配额测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.api.runtime import reset_runtime
from smart_roadtrip.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_quota(monkeypatch):
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


def test_quota_is_per_owner() -> None:
    first = client.post(
        "/chat", json=_payload(), headers={"X-API-Key": "key-a"}
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/chat", json=_payload(), headers={"X-API-Key": "key-a"}
    )
    assert second.status_code == 429
    assert "上限" in second.json()["detail"]

    other = client.post(
        "/chat", json=_payload(), headers={"X-API-Key": "key-b"}
    )
    assert other.status_code == 200, other.text


def test_quota_applies_to_anonymous_in_dev_mode(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_KEYS", "")

    assert client.post("/chat", json=_payload()).status_code == 200
    assert client.post("/chat", json=_payload()).status_code == 429


def test_zero_quota_disables_limit(monkeypatch) -> None:
    monkeypatch.setattr(config, "TENANT_MAX_THREADS_PER_DAY", 0)

    for _ in range(3):
        response = client.post(
            "/chat", json=_payload(), headers={"X-API-Key": "key-a"}
        )
        assert response.status_code == 200, response.text


def test_per_key_quota_override(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_KEYS", "key-a:1,key-b:2")
    monkeypatch.setattr(config, "TENANT_MAX_THREADS_PER_DAY", 0)

    headers_a = {"X-API-Key": "key-a"}
    headers_b = {"X-API-Key": "key-b"}

    assert client.post("/chat", json=_payload(), headers=headers_a).status_code == 200
    assert client.post("/chat", json=_payload(), headers=headers_a).status_code == 429

    assert client.post("/chat", json=_payload(), headers=headers_b).status_code == 200
    assert client.post("/chat", json=_payload(), headers=headers_b).status_code == 200
    assert client.post("/chat", json=_payload(), headers=headers_b).status_code == 429

    assert client.get("/trips/usage", headers=headers_a).json()["limit"] == 1
    assert client.get("/trips/usage", headers=headers_b).json()["limit"] == 2


def test_quota_persists_with_sqlite(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        config, "CHECKPOINT_DB_PATH", str(tmp_path / "quota.sqlite")
    )
    original_backend = config.CHECKPOINTER_BACKEND
    config.CHECKPOINTER_BACKEND = "sqlite"

    try:
        reset_runtime()
        first = client.post(
            "/chat", json=_payload(), headers={"X-API-Key": "key-a"}
        )
        assert first.status_code == 200, first.text

        # 模拟重启：内存状态清空，但 SQLite 中的 owner/created_at 仍在。
        reset_runtime()
        second = client.post(
            "/chat", json=_payload(), headers={"X-API-Key": "key-a"}
        )
        assert second.status_code == 429
    finally:
        config.CHECKPOINTER_BACKEND = original_backend
        reset_runtime()
