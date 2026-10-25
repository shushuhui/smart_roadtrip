"""API 进程内的编排图与线程注册表。

``CHECKPOINTER_BACKEND=sqlite`` 时，进程重启后可通过 checkpointer 恢复线程；
即使内存注册表为空，``thread_exists`` 也会回查图状态。
"""

from __future__ import annotations

import logging
import threading

from smart_roadtrip.api.audit_store import reset_audit_store
from smart_roadtrip.api.ownership import reset_owner_store
from smart_roadtrip.orchestrator.main_agent import (
    build_graph,
    close_graph,
    get_planning_state,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_graph = None
_threads: set[str] = set()


def get_graph():
    global _graph
    with _lock:
        if _graph is None:
            _graph = build_graph()
        return _graph


def reset_runtime() -> None:
    """清空图与已登记线程；测试或本地重启用。"""

    global _graph
    with _lock:
        graph = _graph
        _graph = None
        _threads.clear()
    close_graph(graph)
    reset_owner_store()
    reset_audit_store()


def register_thread(thread_id: str) -> None:
    with _lock:
        _threads.add(thread_id)


def delete_thread(thread_id: str) -> bool:
    """删除 checkpointer 中的线程状态并移出内存注册表。"""

    graph = get_graph()
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is not None and hasattr(checkpointer, "delete_thread"):
        try:
            checkpointer.delete_thread(thread_id)
        except Exception:  # noqa: BLE001 - 状态删除失败不应阻断归属删除
            logger.warning(
                "删除 checkpointer 线程失败：%s", thread_id, exc_info=True
            )
            return False

    with _lock:
        _threads.discard(thread_id)
    return True


def thread_exists(thread_id: str) -> bool:
    with _lock:
        if thread_id in _threads:
            return True

    # 持久化 checkpointer 下，进程重启后注册表为空，但状态仍在。
    state = get_planning_state(thread_id, graph=get_graph())
    if not state:
        return False

    with _lock:
        _threads.add(thread_id)
    return True
