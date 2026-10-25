"""FastAPI 接口测试：规划、确认、查询与路书导出。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smart_roadtrip import config
from smart_roadtrip.api.runtime import reset_runtime
from smart_roadtrip.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_runtime() -> None:
    reset_runtime()
    yield
    reset_runtime()


@pytest.fixture(autouse=True)
def disable_llm(monkeypatch) -> None:
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")


def _payload(*, thread_id: str | None = None, energy_type: str = "ev") -> dict:
    return {
        "thread_id": thread_id,
        "trip": {
            "origin": "成都",
            "destination": "稻城亚丁",
            "days": 3,
            "start_date": "2026-10-26",
            "travelers": [
                {"name": "小张", "is_driver": True, "preferences": ["雪山"]},
                {"name": "小李", "preferences": ["摄影"]},
            ],
            "vehicle": {
                "energy_type": energy_type,
                "rated_range_km": 500,
                "current_soc": 0.8,
            },
            "preferences": {"interests": ["雪山", "摄影"]},
            "budget_cny": 6000,
        },
    }


def _acks(state: dict) -> list[str]:
    pending = state["pending_confirmation"]
    return [
        alert["alert_id"]
        for alert in pending["safety_alerts"]
        if alert["requires_user_confirmation"]
    ]


def _complete_trip() -> tuple[str, dict]:
    body = client.post("/chat", json=_payload()).json()
    thread_id = body["thread_id"]
    approved = client.post(
        f"/trips/{thread_id}/confirm",
        json={
            "decision": "approve",
            "acknowledged_alert_ids": _acks(body["state"]),
        },
    )
    assert approved.status_code == 200, approved.text
    return thread_id, approved.json()


def test_chat_confirm_and_export_report() -> None:
    response = client.post("/chat", json=_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["awaiting_confirmation"] is True
    thread_id = body["thread_id"]
    state = body["state"]
    assert state["thread_id"] == thread_id
    assert state["status"] == "awaiting_confirmation"
    assert state["pending_confirmation"]["question"]

    confirm = client.post(
        f"/trips/{thread_id}/confirm",
        json={
            "decision": "approve",
            "acknowledged_alert_ids": _acks(state),
        },
    )
    assert confirm.status_code == 200, confirm.text
    completed = confirm.json()
    assert completed["status"] == "completed"
    assert completed["confirmed_version_id"] == completed["current_version_id"]
    assert "占位" in completed["report_markdown"]

    fetched = client.get(f"/trips/{thread_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "completed"

    report = client.get(f"/trips/{thread_id}/report")
    assert report.status_code == 200
    assert "占位" in report.json()["markdown"]
    assert report.json()["version_id"] == completed["confirmed_version_id"]


def test_chat_accepts_natural_language() -> None:
    response = client.post(
        "/chat",
        json={"message": "从成都到稻城亚丁玩3天，两个人，开电车"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trip"]["origin"] == "成都"
    assert body["trip"]["destination"] == "稻城亚丁"
    assert body["trip"]["days"] == 3
    assert body["trip"]["vehicle"]["energy_type"] == "ev"
    assert body["state"]["status"] == "awaiting_confirmation"


def test_chat_rejects_unparseable_message() -> None:
    response = client.post("/chat", json={"message": "随便玩玩"})
    assert response.status_code == 422


def test_chat_requires_exactly_one_input() -> None:
    assert client.post("/chat", json={}).status_code == 422
    assert (
        client.post(
            "/chat",
            json={"trip": _payload()["trip"], "message": "从成都到康定玩3天"},
        ).status_code
        == 422
    )


def test_report_before_confirmation_is_not_found() -> None:
    body = client.post("/chat", json=_payload()).json()
    thread_id = body["thread_id"]

    report = client.get(f"/trips/{thread_id}/report")
    assert report.status_code == 404


def test_export_requires_completed_trip() -> None:
    body = client.post("/chat", json=_payload()).json()
    response = client.get(f"/trips/{body['thread_id']}/export?format=md")
    assert response.status_code == 409


def test_export_markdown_and_html() -> None:
    thread_id, _ = _complete_trip()

    markdown = client.get(f"/trips/{thread_id}/export?format=md")
    assert markdown.status_code == 200
    assert "text/markdown" in markdown.headers["content-type"]
    assert "attachment" in markdown.headers["content-disposition"]
    assert "## 每日安排" in markdown.text

    html = client.get(f"/trips/{thread_id}/export?format=html&include_journal=true")
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]
    assert "<!DOCTYPE html>" in html.text
    assert "游记（草稿）" in html.text


def test_export_gpx_requires_coordinates() -> None:
    thread_id, _ = _complete_trip()
    response = client.get(f"/trips/{thread_id}/export?format=gpx")
    assert response.status_code == 409


def test_export_pdf_with_journal() -> None:
    thread_id, _ = _complete_trip()
    response = client.get(
        f"/trips/{thread_id}/export?format=pdf&include_journal=true"
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_journal_requires_completed_trip() -> None:
    body = client.post("/chat", json=_payload()).json()
    thread_id = body["thread_id"]

    journal = client.post(f"/trips/{thread_id}/journal")
    assert journal.status_code == 409


def test_journal_completed_trip_uses_template() -> None:
    body = client.post("/chat", json=_payload()).json()
    thread_id = body["thread_id"]
    approved = client.post(
        f"/trips/{thread_id}/confirm",
        json={
            "decision": "approve",
            "acknowledged_alert_ids": _acks(body["state"]),
        },
    )
    assert approved.status_code == 200

    journal = client.post(f"/trips/{thread_id}/journal")
    assert journal.status_code == 200, journal.text
    data = journal.json()
    assert data["generated_by"] == "template"
    assert "游记（草稿）" in data["markdown"]
    assert data["version_id"] == approved.json()["confirmed_version_id"]


def test_unknown_thread_returns_404() -> None:
    assert client.get("/trips/not-a-thread").status_code == 404
    assert client.get("/trips/not-a-thread/report").status_code == 404
    assert client.get("/trips/not-a-thread/export").status_code == 404
    assert client.post("/trips/not-a-thread/journal").status_code == 404
    assert (
        client.post(
            "/trips/not-a-thread/confirm",
            json={"decision": "approve"},
        ).status_code
        == 404
    )


def test_confirm_requires_awaiting_state() -> None:
    body = client.post("/chat", json=_payload()).json()
    thread_id = body["thread_id"]
    approved = client.post(
        f"/trips/{thread_id}/confirm",
        json={"decision": "approve", "acknowledged_alert_ids": _acks(body["state"])},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"

    again = client.post(
        f"/trips/{thread_id}/confirm",
        json={"decision": "approve"},
    )
    assert again.status_code == 409


def test_duplicate_thread_id_is_rejected() -> None:
    payload = _payload(thread_id="fixed-thread")

    assert client.post("/chat", json=payload).status_code == 200
    assert client.post("/chat", json=payload).status_code == 409


def test_reject_replans_and_skips_energy_for_ice() -> None:
    ev_body = client.post("/chat", json=_payload(energy_type="ice")).json()
    assert "energy" not in ev_body["state"]["tasks"]

    thread_id = ev_body["thread_id"]
    rejected = client.post(
        f"/trips/{thread_id}/confirm",
        json={"decision": "reject", "feedback": "想少开一点车"},
    )
    assert rejected.status_code == 200
    revised = rejected.json()
    assert revised["status"] == "awaiting_confirmation"
    assert revised["round"] == 2
    assert revised["revision_count"] == 1
    assert len(revised["versions"]) == 2
