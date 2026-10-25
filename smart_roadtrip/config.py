"""集中读取 .env 配置。"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]

AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")
CAIYUN_API_KEY = os.getenv("CAIYUN_API_KEY", "")
CHARGING_API_KEY = os.getenv("CHARGING_API_KEY", "")
CHARGING_API_BASE_URL = os.getenv("CHARGING_API_BASE_URL", "")
RESCUE_MEDICAL_API_KEY = os.getenv("RESCUE_MEDICAL_API_KEY", "")
RESCUE_MEDICAL_API_BASE_URL = os.getenv("RESCUE_MEDICAL_API_BASE_URL", "")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

# Open-Meteo 无需 key；测试环境会关闭，避免意外访问外部网络。
OPEN_METEO_ENABLED = os.getenv("OPEN_METEO_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

# RAG 本地知识库
RAG_ENABLED = os.getenv("RAG_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
RAG_EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
RAG_TOP_K = max(1, int(os.getenv("RAG_TOP_K", "5")))
RAG_CHUNK_CHARS = max(100, int(os.getenv("RAG_CHUNK_CHARS", "500")))
RAG_CHUNK_OVERLAP = max(0, int(os.getenv("RAG_CHUNK_OVERLAP", "80")))

# 会话持久化：memory（默认，进程内）或 sqlite（重启后可恢复中断状态）
CHECKPOINTER_BACKEND = os.getenv("CHECKPOINTER_BACKEND", "memory").strip().lower()
CHECKPOINT_DB_PATH = os.getenv(
    "CHECKPOINT_DB_PATH",
    str(PROJECT_ROOT / "data" / "checkpoints.sqlite"),
)

# 工具调用后端：direct（进程内）或 mcp（持久 stdio MCP server）
TOOL_BACKEND = os.getenv("TOOL_BACKEND", "direct").strip().lower()
MCP_MAX_CONCURRENCY = max(1, int(os.getenv("MCP_MAX_CONCURRENCY", "8")))

# 管理员接口：为空时不启用
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
ADMIN_API_KEY_HEADER = os.getenv("ADMIN_API_KEY_HEADER", "X-Admin-Key")

# 日志：text 或 json；json 适合 ELK/Loki 采集
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").strip().lower()

# 请求体大小上限（字节）；仅对 /chat、/trips、/admin 生效
MAX_REQUEST_BYTES = max(
    1024, int(os.getenv("MAX_REQUEST_BYTES", "1000000"))
)

# API 安全：API_KEY / API_KEYS 都为空时开发模式不鉴权
# API_KEYS 支持逗号分隔的多用户 key；每个 key 对应一个独立 owner。
API_KEY = os.getenv("API_KEY", "")
API_KEYS = os.getenv("API_KEYS", "")
API_KEY_HEADER = os.getenv("API_KEY_HEADER", "X-API-Key")
RATE_LIMIT_PER_MINUTE = max(0, int(os.getenv("RATE_LIMIT_PER_MINUTE", "60")))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "")
# 每个 owner 最近 24 小时可创建的行程数；0 表示不限制。
TENANT_MAX_THREADS_PER_DAY = max(
    0, int(os.getenv("TENANT_MAX_THREADS_PER_DAY", "0"))
)

# 审计事件保留天数；0 表示永久保留。
AUDIT_RETENTION_DAYS = max(
    0, int(os.getenv("AUDIT_RETENTION_DAYS", "30"))
)

# 外部工具 HTTP 超时与重试
TOOL_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv("TOOL_TIMEOUT_SECONDS", "10"))
)
TOOL_MAX_RETRIES = max(0, int(os.getenv("TOOL_MAX_RETRIES", "2")))
TOOL_RETRY_BACKOFF_SECONDS = max(
    0.0, float(os.getenv("TOOL_RETRY_BACKOFF_SECONDS", "0.5"))
)

# 费用估算参数
FUEL_PRICE_CNY_PER_L = max(
    0.0, float(os.getenv("FUEL_PRICE_CNY_PER_L", "7.5"))
)
FUEL_CONSUMPTION_L_PER_100KM = max(
    0.0, float(os.getenv("FUEL_CONSUMPTION_L_PER_100KM", "8.0"))
)
ELECTRICITY_PRICE_CNY_PER_KWH = max(
    0.0, float(os.getenv("ELECTRICITY_PRICE_CNY_PER_KWH", "1.2"))
)
EV_CONSUMPTION_KWH_PER_100KM = max(
    0.0, float(os.getenv("EV_CONSUMPTION_KWH_PER_100KM", "16.0"))
)
PHEV_FUEL_SHARE = min(
    1.0, max(0.0, float(os.getenv("PHEV_FUEL_SHARE", "0.5")))
)
