"""FastAPI 应用入口。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from smart_roadtrip import config
from smart_roadtrip.api import admin, chat, trips
from smart_roadtrip.api.observability import (
    RequestLogMiddleware,
    configure_logging,
    metrics_snapshot,
)
from smart_roadtrip.api.security import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    configured_api_keys,
)
from smart_roadtrip.rag import retriever

configure_logging()

app = FastAPI(
    title="基于多智能体协作的旅行规划系统 API",
    version="1.0.0",
    description=(
        "智能自驾游多 Agent 行程规划系统：自然语言/结构化需求、"
        "人在回路确认、动态重排、RAG 知识库、多租户与导出。"
    ),
    openapi_tags=[
        {"name": "chat", "description": "创建行程与需求解析"},
        {"name": "trips", "description": "行程查询、确认、删除、导出"},
        {"name": "admin", "description": "管理员跨租户用量汇总"},
        {"name": "ops", "description": "健康检查、readiness 与指标"},
    ],
)

app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

_origins = [
    origin.strip()
    for origin in config.CORS_ORIGINS.split(",")
    if origin.strip()
]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# 最后添加，作为最外层中间件，确保所有请求都有 request id 与指标。
app.add_middleware(RequestLogMiddleware)


@app.get("/health", tags=["ops"], summary="健康检查")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", tags=["ops"], summary="进程内请求指标")
def metrics() -> dict:
    """进程内累计请求指标；不包含密钥或请求体。"""

    return metrics_snapshot()


@app.get("/ready", tags=["ops"], summary="readiness 检查")
def ready() -> dict:
    checks: dict[str, object] = {
        "checkpointer_backend": config.CHECKPOINTER_BACKEND,
        "api_auth_enabled": bool(configured_api_keys()),
        "rate_limit_per_minute": config.RATE_LIMIT_PER_MINUTE,
        "amap_configured": bool(config.AMAP_API_KEY),
        "caiyun_configured": bool(config.CAIYUN_API_KEY),
        "open_meteo_enabled": config.OPEN_METEO_ENABLED,
        "rag_enabled": config.RAG_ENABLED,
        "rag_index_available": retriever.is_available(),
        "llm_configured": bool(config.LLM_API_KEY and config.LLM_MODEL),
    }
    if config.CHECKPOINTER_BACKEND == "sqlite":
        db_path = Path(config.CHECKPOINT_DB_PATH)
        if not db_path.is_absolute():
            db_path = config.PROJECT_ROOT / db_path
        checks["checkpoint_db_exists"] = db_path.is_file()

    return {"status": "ready", "checks": checks}


app.include_router(chat.router)
app.include_router(trips.router)
app.include_router(admin.router)
