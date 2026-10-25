"""租户审计事件存储。

memory checkpointer 下使用进程内列表；sqlite checkpointer 下复用同一个
SQLite 文件的 ``audit_events`` 表，服务重启后仍可查询。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smart_roadtrip import config

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    event TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_audit_owner_created
ON audit_events(owner, created_at DESC)
"""

_INDEX_CREATED_SQL = """
CREATE INDEX IF NOT EXISTS idx_audit_created
ON audit_events(created_at)
"""

_MAX_MEMORY_EVENTS = 5000


class AuditStore:
    def __init__(self, db_path: Path | None):
        self._lock = threading.Lock()
        self._memory: list[dict[str, str]] = []
        self._conn: sqlite3.Connection | None = None
        self._writes = 0

        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(db_path), check_same_thread=False, timeout=10
            )
            with self._lock:
                self._conn.execute(_TABLE_SQL)
                self._conn.execute(_INDEX_SQL)
                self._conn.execute(_INDEX_CREATED_SQL)
                self._conn.commit()

    def record(
        self,
        *,
        owner: str,
        event: str,
        thread_id: str = "",
        detail: str = "",
        created_at: datetime | None = None,
    ) -> None:
        timestamp = (created_at or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            if self._conn is None:
                self._memory.append(
                    {
                        "owner": owner,
                        "event": event,
                        "thread_id": thread_id,
                        "detail": detail,
                        "created_at": timestamp,
                    }
                )
                if len(self._memory) > _MAX_MEMORY_EVENTS:
                    del self._memory[: len(self._memory) - _MAX_MEMORY_EVENTS]
                self._maybe_purge_locked()
                return

            self._conn.execute(
                """
                INSERT INTO audit_events(owner, event, thread_id, detail, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (owner, event, thread_id, detail, timestamp),
            )
            self._conn.commit()
            self._maybe_purge_locked()

    def purge_before(self, cutoff: datetime) -> int:
        with self._lock:
            return self._purge_locked(cutoff)

    def _maybe_purge_locked(self) -> None:
        self._writes += 1
        retention = config.AUDIT_RETENTION_DAYS
        if retention <= 0 or self._writes % 100 != 0:
            return
        self._purge_locked(
            datetime.now(timezone.utc) - timedelta(days=retention)
        )

    def _purge_locked(self, cutoff: datetime) -> int:
        cutoff_text = cutoff.isoformat()
        if self._conn is None:
            before = len(self._memory)
            self._memory = [
                item
                for item in self._memory
                if item["created_at"] >= cutoff_text
            ]
            return before - len(self._memory)

        cursor = self._conn.execute(
            "DELETE FROM audit_events WHERE created_at < ?",
            (cutoff_text,),
        )
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def list_for_owner(
        self,
        owner: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, str]]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._lock:
            if self._conn is None:
                rows = [
                    {
                        "event": item["event"],
                        "thread_id": item["thread_id"],
                        "detail": item["detail"],
                        "created_at": item["created_at"],
                    }
                    for item in reversed(self._memory)
                    if item["owner"] == owner
                ]
                return rows[offset : offset + limit]

            cursor = self._conn.execute(
                """
                SELECT event, thread_id, detail, created_at
                FROM audit_events
                WHERE owner = ?
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (owner, limit, offset),
            )
            return [
                {
                    "event": str(row[0]),
                    "thread_id": str(row[1]),
                    "detail": str(row[2]),
                    "created_at": str(row[3]),
                }
                for row in cursor.fetchall()
            ]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


_store: AuditStore | None = None
_store_lock = threading.Lock()


def _audit_path() -> Path | None:
    backend = (config.CHECKPOINTER_BACKEND or "memory").strip().lower()
    if backend != "sqlite":
        return None
    path = Path(config.CHECKPOINT_DB_PATH)
    if not path.is_absolute():
        path = config.PROJECT_ROOT / path
    return path


def get_audit_store() -> AuditStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = AuditStore(_audit_path())
        return _store


def reset_audit_store() -> None:
    global _store
    with _store_lock:
        store = _store
        _store = None
    if store is not None:
        store.close()
