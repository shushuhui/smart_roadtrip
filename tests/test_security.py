"""API 鉴权、限流与 readiness 测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.api.runtime import reset_runtime
from smart_roadtrip.api.security import reset_rate_limiter
from smart_roadtrip.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_security_state(monkeypatch):
    reset_runtime()
    reset_rate_limiter()
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0)
    yield
    reset_runtime()
    reset_rate_limiter()


def test_auth_disabled_by_default() -> None:
    response = client.get("/trips/not-a-thread")
    assert response.status_code == 404


def test_api_key_required_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_KEY", "secret")

    assert client.get("/trips/not-a-thread").status_code == 401
    assert (
        client.get(
            "/trips/not-a-thread", headers={"X-API-Key": "wrong"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/trips/not-a-thread", headers={"X-API-Key": "secret"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/chat", json={}, headers={"X-API-Key": "secret"}
        ).status_code
        == 422
    )

    # 健康检查与 readiness 不要求鉴权。
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_rate_limit_returns_429(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_KEY", "secret")
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 2)
    reset_rate_limiter()
    headers = {"X-API-Key": "secret"}

    assert client.get("/trips/missing", headers=headers).status_code == 404
    assert client.get("/trips/missing", headers=headers).status_code == 404

    limited = client.get("/trips/missing", headers=headers)
    assert limited.status_code == 429
    assert limited.headers.get("Retry-After") == "60"

    # /health 不受限流影响。
    assert client.get("/health").status_code == 200


def test_ready_reports_multi_key_auth(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEYS", "key-a:10,key-b")

    payload = client.get("/ready").json()
    assert payload["checks"]["api_auth_enabled"] is True


def test_ready_reports_auth_disabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEYS", "")

    payload = client.get("/ready").json()
    assert payload["checks"]["api_auth_enabled"] is False


def test_ready_endpoint_reports_checks() -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert {
        "checkpointer_backend",
        "api_auth_enabled",
        "rate_limit_per_minute",
        "amap_configured",
        "caiyun_configured",
        "open_meteo_enabled",
        "rag_enabled",
        "rag_index_available",
        "llm_configured",
    } <= set(payload["checks"])
