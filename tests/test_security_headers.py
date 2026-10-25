"""安全响应头与请求体大小限制测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.main import app

client = TestClient(app)


def test_security_headers_are_added() -> None:
    response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_oversized_body_returns_413(monkeypatch) -> None:
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", 1024)
    body = "{" + '"x":"' + "a" * 2000 + '"}'

    response = client.post(
        "/chat",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert "请求体过大" in response.json()["detail"]


def test_health_is_not_body_limited(monkeypatch) -> None:
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", 1024)

    assert client.get("/health").status_code == 200
