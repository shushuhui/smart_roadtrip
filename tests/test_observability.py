"""请求 ID、指标与审计日志测试。"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.api.observability import reset_metrics
from smart_roadtrip.api.runtime import reset_runtime
from smart_roadtrip.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_observability(monkeypatch):
    reset_runtime()
    reset_metrics()
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEYS", "")
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0)
    yield
    reset_runtime()
    reset_metrics()


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


def test_request_id_header_is_added_and_preserved() -> None:
    response = client.get("/health")
    assert response.headers.get("x-request-id")

    custom = client.get("/health", headers={"X-Request-ID": "req-123"})
    assert custom.headers["x-request-id"] == "req-123"


def test_metrics_counts_requests() -> None:
    reset_metrics()
    client.get("/health")
    client.get("/metrics")

    # 当前 /metrics 请求在快照之后才计数，因此第二次读取可看到前两次请求。
    data = client.get("/metrics").json()
    assert data["requests_total"] >= 2
    assert data["by_status"].get("200", 0) >= 2
    assert data["avg_duration_ms"] >= 0


def test_audit_logs_thread_created(caplog) -> None:
    caplog.set_level(logging.INFO, logger="smart_roadtrip.audit")

    response = client.post("/chat", json=_payload())
    assert response.status_code == 200, response.text
    assert "audit event=thread_created" in caplog.text
    assert "owner=anonymous" in caplog.text
