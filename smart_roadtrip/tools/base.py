"""工具层公共错误与 HTTP 辅助。

工具层只负责外部服务的请求与响应解析；业务判断留在 Agent 与编排层。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from smart_roadtrip import config

DEFAULT_TIMEOUT_SECONDS = 10.0


class ToolError(RuntimeError):
    """外部工具调用失败的基类。"""


class ToolUnavailableError(ToolError):
    """未配置密钥或供应商不可用；Agent 应降级到占位结果。"""


class ToolResponseError(ToolError):
    """外部服务返回错误或格式不符合预期。"""


def require_key(value: str, env_name: str) -> str:
    if not value:
        raise ToolUnavailableError(
            f"未配置 {env_name}，已跳过外部服务调用并降级为占位数据。"
        )
    return value


def get_client(timeout: float | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=timeout if timeout is not None else config.TOOL_TIMEOUT_SECONDS
    )


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """GET 并返回 JSON 对象；网络错误、429、5xx 会按配置重试。

    错误信息刻意不拼接完整 URL，避免把查询参数中的 API key 写进日志。
    """

    attempts = max(1, config.TOOL_MAX_RETRIES + 1)
    backoff = config.TOOL_RETRY_BACKOFF_SECONDS
    last_error: ToolResponseError | None = None

    for attempt in range(attempts):
        try:
            response = client.get(url, params=params, headers=headers)
        except httpx.RequestError as exc:
            last_error = ToolResponseError(
                f"外部服务请求失败（{type(exc).__name__}）。"
            )
            if attempt + 1 < attempts:
                time.sleep(backoff * (2**attempt))
                continue
            raise last_error from exc

        if response.status_code == 429 or response.status_code >= 500:
            last_error = ToolResponseError(
                f"外部服务暂时不可用（HTTP {response.status_code}）。"
            )
            if attempt + 1 < attempts:
                time.sleep(backoff * (2**attempt))
                continue
            raise last_error

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ToolResponseError(
                f"外部服务返回错误（HTTP {exc.response.status_code}）。"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ToolResponseError("外部服务返回了非 JSON 内容。") from exc

        if not isinstance(payload, dict):
            raise ToolResponseError("外部服务返回格式不正确。")
        return payload

    raise last_error or ToolResponseError("外部服务请求失败。")
