"""审计事件保留策略测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from smart_roadtrip.api.audit_store import AuditStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_memory_retention_purges_old_events() -> None:
    store = AuditStore(None)
    store.record(
        owner="key-a",
        event="old",
        created_at=_now() - timedelta(days=40),
    )
    store.record(owner="key-a", event="new")

    removed = store.purge_before(_now() - timedelta(days=30))

    assert removed == 1
    assert [item["event"] for item in store.list_for_owner("key-a")] == [
        "new"
    ]


def test_sqlite_retention_purges_old_events(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "audit.sqlite")
    try:
        store.record(
            owner="key-a",
            event="old",
            created_at=_now() - timedelta(days=40),
        )
        store.record(owner="key-a", event="new")

        removed = store.purge_before(_now() - timedelta(days=30))

        assert removed == 1
        assert [item["event"] for item in store.list_for_owner("key-a")] == [
            "new"
        ]
    finally:
        store.close()
