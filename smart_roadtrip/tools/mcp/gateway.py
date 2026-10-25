"""持久化 MCP stdio 会话。

在一个后台线程的事件循环里保持单个 ``ClientSession``，避免每次工具调用都
启动新的 server 进程。同步调用通过线程安全队列串行执行。
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from smart_roadtrip import config
from smart_roadtrip.tools.base import ToolError, ToolUnavailableError
from smart_roadtrip.tools.mcp.client import convert_call_result, server_params


class MCPToolError(ToolError):
    """MCP server 返回了工具级错误。"""


class MCPUnavailableError(ToolUnavailableError):
    """MCP server 无法启动或连接中断。"""


class MCPGateway:
    def __init__(
        self,
        *,
        startup_timeout: float = 30.0,
        call_timeout: float = 60.0,
    ):
        self._startup_timeout = startup_timeout
        self._call_timeout = call_timeout
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._loop_started = threading.Event()
        self._error: BaseException | None = None
        self._closed = False
        self._queue: asyncio.Queue | None = None
        self._worker_future = None

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="mcp-gateway",
            daemon=True,
        )
        self._thread.start()
        if not self._loop_started.wait(5):
            raise MCPUnavailableError("MCP gateway 事件循环启动超时。")
        self._worker_future = asyncio.run_coroutine_threadsafe(
            self._worker(), self._loop
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop_started.set()
        self._loop.run_forever()

    async def _worker(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        self._queue = queue
        semaphore = asyncio.Semaphore(max(1, config.MCP_MAX_CONCURRENCY))
        tasks: set[asyncio.Task] = set()

        async def handle(item) -> None:
            future, name, arguments = item
            if future.cancelled():
                return
            async with semaphore:
                try:
                    result = await session.call_tool(name, arguments or {})
                except Exception as exc:  # noqa: BLE001
                    future.set_exception(exc)
                else:
                    future.set_result(result)

        try:
            async with stdio_client(server_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._ready.set()
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        task = asyncio.create_task(handle(item))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    if tasks:
                        await asyncio.gather(
                            *tasks, return_exceptions=True
                        )
        except BaseException as exc:  # noqa: BLE001 - 记录启动/运行错误
            self._error = exc
        finally:
            self._ready.set()

    def _ensure_ready(self) -> None:
        if self._closed:
            raise MCPUnavailableError("MCP gateway 已关闭。")
        if not self._ready.wait(self._startup_timeout):
            raise MCPUnavailableError("MCP server 启动超时。")
        if self._error is not None:
            raise MCPUnavailableError(
                f"MCP server 不可用：{type(self._error).__name__}"
            )
        if self._worker_future is not None and self._worker_future.done():
            exc = self._worker_future.exception()
            raise MCPUnavailableError(
                f"MCP server 已退出：{type(exc).__name__ if exc else 'unknown'}"
            )

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        with self._lock:
            self._ensure_ready()
        if self._queue is None:
            raise MCPUnavailableError("MCP gateway 队列尚未就绪。")

        async def enqueue():
            future = self._loop.create_future()
            await self._queue.put((future, name, arguments or {}))
            return await future

        submission = asyncio.run_coroutine_threadsafe(
            enqueue(), self._loop
        )
        try:
            result = submission.result(timeout=self._call_timeout)
        except Exception as exc:  # noqa: BLE001 - 连接/超时错误
            raise MCPUnavailableError(
                f"MCP 调用失败：{type(exc).__name__}"
            ) from exc

        try:
            return convert_call_result(result)
        except RuntimeError as exc:
            raise MCPToolError(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

            if self._queue is not None and self._loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._queue.put(None), self._loop
                    ).result(timeout=5)
                except Exception:  # noqa: BLE001
                    pass

            if self._worker_future is not None:
                try:
                    self._worker_future.result(timeout=10)
                except Exception:  # noqa: BLE001
                    pass

            if self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread.is_alive():
                self._thread.join(timeout=2)


_gateway: MCPGateway | None = None
_gateway_lock = threading.Lock()


def get_gateway() -> MCPGateway:
    global _gateway
    with _gateway_lock:
        if _gateway is None or _gateway.closed:
            _gateway = MCPGateway()
        return _gateway


def reset_gateway() -> None:
    global _gateway
    with _gateway_lock:
        gateway = _gateway
        _gateway = None
    if gateway is not None:
        gateway.close()


atexit.register(reset_gateway)
