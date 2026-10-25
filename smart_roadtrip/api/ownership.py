"""线程归属存储：把 ``thread_id`` 绑定到 API key 对应的 owner。

memory checkpointer 下使用进程内字典；sqlite checkpointer 下复用同一个
SQLite 文件中的独立表，服务重启后仍能校验归属。
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from smart_roadtrip import config
from smart_roadtrip.api.security import configured_api_keys

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS thread_owners (
    thread_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


class ThreadOwners:
    def __init__(self, db_path: Path | None):
        self._db_path = db_path
        self._lock = threading.Lock()
        # thread_id -> (owner, created_at ISO)
        self._memory: dict[str, tuple[str, str]] = {}
        self._conn: sqlite3.Connection | None = None

        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(db_path), check_same_thread=False, timeout=10
            )
            with self._lock:
                self._conn.execute(_TABLE_SQL)
                self._conn.commit()

    def set(self, thread_id: str, owner: str) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if self._conn is None:
                self._memory.setdefault(thread_id, (owner, created_at))
                return
            self._conn.execute(
                """
                INSERT INTO thread_owners(thread_id, owner, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(thread_id) DO NOTHING
                """,
                (thread_id, owner, created_at),
            )
            self._conn.commit()

    def check(self, thread_id: str, owner: str) -> bool:
        if not owner:
            return False

        with self._lock:
            if self._conn is None:
                entry = self._memory.get(thread_id)
                stored = entry[0] if entry else None
            else:
                row = self._conn.execute(
                    "SELECT owner FROM thread_owners WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                stored = row[0] if row else None

        if stored is None:
            # 兼容升级前创建、尚未登记归属的线程；仅在开发模式放行。
            return not configured_api_keys()
        return secrets.compare_digest(stored, owner)

    def count_since(self, owner: str, since: datetime) -> int:
        """统计某 owner 在 ``since`` 之后创建的线程数。"""

        cutoff = since.isoformat()
        with self._lock:
            if self._conn is None:
                return sum(
                    1
                    for stored_owner, created_at in self._memory.values()
                    if stored_owner == owner and created_at >= cutoff
                )
            row = self._conn.execute(
                """
                SELECT COUNT(*)
                FROM thread_owners
                WHERE owner = ? AND created_at >= ?
                """,
                (owner, cutoff),
            ).fetchone()
            return int(row[0]) if row else 0

    def delete(self, thread_id: str, owner: str) -> bool:
        """删除归属记录；只有 owner 匹配时才删除。"""

        with self._lock:
            if self._conn is None:
                entry = self._memory.get(thread_id)
                if entry is None or entry[0] != owner:
                    return False
                del self._memory[thread_id]
                return True

            cursor = self._conn.execute(
                "DELETE FROM thread_owners WHERE thread_id = ? AND owner = ?",
                (thread_id, owner),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def owner_summaries(self) -> list[tuple[str, int]]:
        """返回 ``(owner, thread_count)``，按线程数倒序。"""

        with self._lock:
            if self._conn is None:
                counts: dict[str, int] = {}
                for stored_owner, _ in self._memory.values():
                    counts[stored_owner] = counts.get(stored_owner, 0) + 1
                return sorted(
                    counts.items(), key=lambda item: (-item[1], item[0])
                )

            rows = self._conn.execute(
                """
                SELECT owner, COUNT(*)
                FROM thread_owners
                GROUP BY owner
                ORDER BY COUNT(*) DESC, owner ASC
                """
            ).fetchall()
            return [(str(row[0]), int(row[1])) for row in rows]

    def count_for_owner(self, owner: str) -> int:
        with self._lock:
            if self._conn is None:
                return sum(
                    1
                    for stored_owner, _ in self._memory.values()
                    if stored_owner == owner
                )
            row = self._conn.execute(
                "SELECT COUNT(*) FROM thread_owners WHERE owner = ?",
                (owner,),
            ).fetchone()
            return int(row[0]) if row else 0

    def list_for_owner(
        self,
        owner: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[tuple[str, str]]:
        """按创建时间倒序返回 ``(thread_id, created_at)``。"""

        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self._lock:
            if self._conn is None:
                rows = [
                    (thread_id, created_at)
                    for thread_id, (stored_owner, created_at) in self._memory.items()
                    if stored_owner == owner
                ]
                rows.sort(key=lambda item: item[1], reverse=True)
                return rows[offset : offset + limit]

            rows = self._conn.execute(
                """
                SELECT thread_id, created_at
                FROM thread_owners
                WHERE owner = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (owner, limit, offset),
            ).fetchall()
            return [(str(row[0]), str(row[1])) for row in rows]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


_store: ThreadOwners | None = None
_store_lock = threading.Lock()


def _ownership_path() -> Path | None:
    backend = (config.CHECKPOINTER_BACKEND or "memory").strip().lower()
    if backend != "sqlite":
        return None
    path = Path(config.CHECKPOINT_DB_PATH)
    if not path.is_absolute():
        path = config.PROJECT_ROOT / path
    return path


def get_owner_store() -> ThreadOwners:
    global _store
    with _store_lock:
        if _store is None:
            _store = ThreadOwners(_ownership_path())
        return _store


def reset_owner_store() -> None:
    global _store
    with _store_lock:
        store = _store
        _store = None
    if store is not None:
        store.close()
