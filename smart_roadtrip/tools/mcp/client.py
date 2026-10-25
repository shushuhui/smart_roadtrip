"""MCP 客户端：启动 stdio server 并调用暴露的工具。

同步包装器使用 ``asyncio.run``，不要在已有事件循环中调用；异步场景请直接
使用 ``list_tools_async`` / ``call_tool_async``。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from smart_roadtrip import config

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVER_SCRIPT = Path(__file__).with_name("server.py")


def server_params() -> StdioServerParameters:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # 把当前进程的配置显式传给 server，保证测试/运行时的 key 与开关一致。
    env.update(
        {
            "AMAP_API_KEY": config.AMAP_API_KEY,
            "CAIYUN_API_KEY": config.CAIYUN_API_KEY,
            "CHARGING_API_KEY": config.CHARGING_API_KEY,
            "CHARGING_API_BASE_URL": config.CHARGING_API_BASE_URL,
            "RESCUE_MEDICAL_API_KEY": config.RESCUE_MEDICAL_API_KEY,
            "RESCUE_MEDICAL_API_BASE_URL": config.RESCUE_MEDICAL_API_BASE_URL,
            "LLM_API_KEY": config.LLM_API_KEY,
            "LLM_BASE_URL": config.LLM_BASE_URL,
            "LLM_MODEL": config.LLM_MODEL,
            "OPEN_METEO_ENABLED": "1" if config.OPEN_METEO_ENABLED else "0",
            "RAG_ENABLED": "1" if config.RAG_ENABLED else "0",
            "RAG_EMBEDDING_MODEL": config.RAG_EMBEDDING_MODEL,
            "RAG_TOP_K": str(config.RAG_TOP_K),
        }
    )
    return StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
        cwd=str(PROJECT_ROOT),
        env=env,
    )


async def list_tools_async() -> list[dict[str, Any]]:
    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [tool.model_dump(mode="json") for tool in result.tools]


async def call_tool_async(
    name: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments or {})
            return convert_call_result(result)


def convert_call_result(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)

    blocks = [
        block.model_dump(mode="json")
        for block in getattr(result, "content", [])
    ]
    is_error = bool(
        getattr(result, "isError", False)
        or getattr(result, "is_error", False)
    )
    if is_error:
        message = "；".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("text")
        )
        raise RuntimeError(message or "MCP 工具调用失败。")

    if structured is not None:
        return structured

    # 单条文本结果通常是 JSON；尽量解析成 Python 对象。
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        text = str(blocks[0].get("text", ""))
        try:
            return json.loads(text)
        except ValueError:
            return text
    return blocks


def list_tools() -> list[dict[str, Any]]:
    return asyncio.run(list_tools_async())


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    return asyncio.run(call_tool_async(name, arguments))


# 兼容旧名称
_call_result = convert_call_result
