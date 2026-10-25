"""测试全局隔离：禁止测试意外调用真实外部服务。"""

from __future__ import annotations

import pytest

from smart_roadtrip import config

_EXTERNAL_SETTINGS = (
    "AMAP_API_KEY",
    "CAIYUN_API_KEY",
    "CHARGING_API_KEY",
    "CHARGING_API_BASE_URL",
    "RESCUE_MEDICAL_API_KEY",
    "RESCUE_MEDICAL_API_BASE_URL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
)


@pytest.fixture(autouse=True)
def disable_external_services(monkeypatch) -> None:
    for name in _EXTERNAL_SETTINGS:
        monkeypatch.setattr(config, name, "")
    # Open-Meteo 无需 key，但测试也必须禁止默认网络访问；
    # 工具测试通过显式传入 httpx.MockTransport client 来验证解析逻辑。
    monkeypatch.setattr(config, "OPEN_METEO_ENABLED", False)
    # RAG 默认关闭，避免测试加载真实 embedding 模型；RAG 测试直接使用
    # RagRetriever + 假 embedder。
    monkeypatch.setattr(config, "RAG_ENABLED", False)
    # 默认使用进程内 checkpointer；持久化测试显式传入 backend="sqlite"。
    monkeypatch.setattr(config, "CHECKPOINTER_BACKEND", "memory")
    # 默认工具直连；MCP 模式测试会显式切换并重置 gateway。
    monkeypatch.setattr(config, "TOOL_BACKEND", "direct")
    # 测试默认关闭鉴权与限流；安全测试会显式开启。
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "API_KEYS", "")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "")
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(config, "TENANT_MAX_THREADS_PER_DAY", 0)
    monkeypatch.setattr(config, "AUDIT_RETENTION_DAYS", 0)
    monkeypatch.setattr(config, "TOOL_MAX_RETRIES", 0)
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", 1_000_000)
    monkeypatch.setattr(config, "TOOL_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(config, "CORS_ORIGINS", "")
    yield
