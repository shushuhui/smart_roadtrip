"""SQLite checkpointer 持久化测试。"""

from __future__ import annotations

from datetime import date

from smart_roadtrip import config
from smart_roadtrip.api import runtime
from smart_roadtrip.orchestrator.main_agent import (
    build_graph,
    close_graph,
    get_planning_state,
    resume_planning,
    start_planning,
)
from smart_roadtrip.schemas.trip import TripRequest, TripStatus, Traveler


def _request() -> TripRequest:
    return TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=2,
        start_date=date(2026, 11, 1),
        travelers=[Traveler(name="我", is_driver=True)],
    )


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


def test_sqlite_state_survives_new_graph(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "checkpoints.sqlite"
    monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(db_path))
    thread_id = "persist-test"

    graph1 = build_graph(backend="sqlite")
    try:
        first = start_planning(_request(), thread_id, graph=graph1)
        assert "__interrupt__" in first
        assert first["status"] == TripStatus.AWAITING_CONFIRMATION
        assert first["pending_confirmation"] is not None
        acks = [
            alert.alert_id
            for alert in first["pending_confirmation"].safety_alerts
            if alert.requires_user_confirmation
        ]
    finally:
        close_graph(graph1)

    assert db_path.exists()

    graph2 = build_graph(backend="sqlite")
    try:
        restored = get_planning_state(thread_id, graph=graph2)
        assert restored["status"] == TripStatus.AWAITING_CONFIRMATION
        assert restored["current_version_id"] == first["current_version_id"]

        final = resume_planning(
            {"decision": "approve", "acknowledged_alert_ids": acks},
            thread_id,
            graph=graph2,
        )
        assert final["status"] == TripStatus.COMPLETED
        assert final["report_markdown"]
    finally:
        close_graph(graph2)


def test_api_thread_lookup_falls_back_to_persisted_state(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "api-checkpoints.sqlite"
    monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(db_path))
    original_backend = config.CHECKPOINTER_BACKEND
    config.CHECKPOINTER_BACKEND = "sqlite"
    thread_id = "api-persist-test"

    try:
        runtime.reset_runtime()
        graph = runtime.get_graph()
        start_planning(_request(), thread_id, graph=graph)

        # 模拟进程重启：清空内存图与线程注册表，但 SQLite 文件仍在。
        runtime.reset_runtime()

        assert runtime.thread_exists(thread_id) is True
        state = get_planning_state(thread_id, graph=runtime.get_graph())
        assert state["status"] == TripStatus.AWAITING_CONFIRMATION
    finally:
        config.CHECKPOINTER_BACKEND = original_backend
        runtime.reset_runtime()


def test_owner_persists_with_sqlite(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from smart_roadtrip.main import app

    client = TestClient(app)
    db_path = tmp_path / "owner-checkpoints.sqlite"
    monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", str(db_path))
    monkeypatch.setattr(config, "API_KEYS", "key-a")
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0)
    original_backend = config.CHECKPOINTER_BACKEND
    config.CHECKPOINTER_BACKEND = "sqlite"

    try:
        runtime.reset_runtime()
        created = client.post(
            "/chat", json=_payload(), headers={"X-API-Key": "key-a"}
        )
        assert created.status_code == 200, created.text
        thread_id = created.json()["thread_id"]

        # 模拟进程重启：图和 owner store 都重建，但 SQLite 文件仍在。
        runtime.reset_runtime()

        restored = client.get(
            f"/trips/{thread_id}", headers={"X-API-Key": "key-a"}
        )
        assert restored.status_code == 200, restored.text
    finally:
        config.CHECKPOINTER_BACKEND = original_backend
        runtime.reset_runtime()
