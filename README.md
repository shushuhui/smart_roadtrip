# 基于多智能体协作的旅行规划系统（Smart Roadtrip）

智能自驾游多 Agent 行程规划系统。用户输入自然语言或结构化需求，MainAgent 调度路线、天气、补能、知识库、安全、多人协同等子 Agent，生成可确认、可重排、可导出的中文行程。

开发约定、模块细节与测试说明见 [CLAUDE.md](CLAUDE.md)。

## 功能

- 自然语言需求解析：LLM 可选，失败自动回退规则解析
- 路线规划：高德驾车路线 / 多方案 / 驾驶负荷
- 天气：彩云（可选）或免 key 的 Open-Meteo
- 电车补能：高德 POI 充电站；配置商业供应商后可获取实时价格/排队/可用枪数
- 安全护航：高德 POI 医院/药店/汽车维修；配置商业救援供应商后可获取商业资源；安全提醒需用户逐项确认
- 本地 RAG：FAISS + sentence-transformers，内置 11 份示例语料，按 `activities/destinations/energy/equipment/family/regulations/routes/safety` 分类存放，可直接替换；旧版语料归档在 `data/knowledge_archive`（不参与索引）
- 人在回路：LangGraph `interrupt()` 暂停/恢复确认
- 动态重排与版本回滚
- 费用估算：能源费 + 过路费，并按同行人数均摊（不含住宿/餐饮/门票）
- 游记草稿：LLM 可选，失败回退模板
- 导出：Markdown / HTML / GPX / PDF
- 多租户：多 API key、线程归属隔离、创建配额、审计查询、管理员跨租户用量汇总
- 持久化：SQLite checkpointer（可选）
- Python HTTP 客户端 + CLI（`RoadtripClient` / `scripts/roadtrip_cli.py`）
- MCP stdio server/client，Tools 可在 direct / mcp 后端间切换
- 可观测性：请求 ID、进程内指标、审计事件

## 快速开始

```bash
conda activate smart_roadtrip
pip install -r requirements-dev.txt

cp .env.example .env          # Windows: copy .env.example .env
# 编辑 .env，至少填写实际可用的 AMAP_API_KEY；LLM 可选

python scripts/ingest_knowledge.py   # 构建/重建本地 RAG 索引（可选；模型优先用本地缓存）
uvicorn smart_roadtrip.main:app --reload       # API: http://127.0.0.1:8000
streamlit run smart_roadtrip/ui/streamlit_app.py  # UI: http://localhost:8501
```

健康检查与文档：

- `GET /health`
- `GET /ready`
- `GET /metrics`
- `GET /docs`：Swagger UI，含中文 tags、路由摘要与请求示例

## 配置

`.env` 从 `.env.example` 复制；模板只保留空占位，真实密钥不要提交。

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `AMAP_API_KEY` | 高德路线/地理编码/POI | 空 |
| `CAIYUN_API_KEY` | 彩云天气（可选） | 空 |
| `CHARGING_API_BASE_URL` / `CHARGING_API_KEY` | 商业充电网络（可选） | 空 |
| `RESCUE_MEDICAL_API_BASE_URL` / `RESCUE_MEDICAL_API_KEY` | 商业救援/医疗（可选） | 空 |
| `OPEN_METEO_ENABLED` | 使用免 key 的 Open-Meteo | `1` |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | OpenAI 兼容 LLM | 空 |
| `RAG_ENABLED` | 启用本地知识库检索 | `1` |
| `RAG_EMBEDDING_MODEL` | embedding 模型 | `BAAI/bge-small-zh-v1.5` |
| `RAG_TOP_K` | 检索片段数 | `5` |
| `RAG_CHUNK_CHARS` / `RAG_CHUNK_OVERLAP` | 切分片段长度 / 重叠字符数 | `500` / `80` |
| `CHECKPOINTER_BACKEND` | `memory` 或 `sqlite` | `memory` |
| `CHECKPOINT_DB_PATH` | SQLite 文件路径 | `data/checkpoints.sqlite` |
| `TOOL_BACKEND` | `direct` 或 `mcp` | `direct` |
| `MCP_MAX_CONCURRENCY` | MCP 工具并发上限 | `8` |
| `API_KEY` / `API_KEYS` | 单 key / 多 key（逗号分隔，支持 `key:24h_limit`） | 空（开发模式） |
| `API_KEY_HEADER` | API key 请求头 | `X-API-Key` |
| `ADMIN_API_KEY` / `ADMIN_API_KEY_HEADER` | 管理员接口 key / 请求头 | 空 / `X-Admin-Key` |
| `LOG_LEVEL` / `LOG_FORMAT` | 日志级别与格式（text/json） | `INFO` / `text` |
| `MAX_REQUEST_BYTES` | `/chat`、`/trips`、`/admin` 请求体上限 | `1000000` |
| `RATE_LIMIT_PER_MINUTE` | 每 key/IP 每分钟请求数 | `60` |
| `TENANT_MAX_THREADS_PER_DAY` | 每 owner 24 小时创建上限 | `0`（不限） |
| `AUDIT_RETENTION_DAYS` | 审计事件保留天数（0=永久） | `30` |
| `TOOL_TIMEOUT_SECONDS` / `TOOL_MAX_RETRIES` / `TOOL_RETRY_BACKOFF_SECONDS` | 外部工具 HTTP 超时、重试次数与指数退避基数（秒） | `10` / `2` / `0.5` |
| `FUEL_PRICE_CNY_PER_L` / `FUEL_CONSUMPTION_L_PER_100KM` | 燃油费用估算 | `7.5` / `8.0` |
| `ELECTRICITY_PRICE_CNY_PER_KWH` / `EV_CONSUMPTION_KWH_PER_100KM` | 电费估算 | `1.2` / `16.0` |
| `PHEV_FUEL_SHARE` | 插混燃油占比 | `0.5` |
| `CORS_ORIGINS` | 允许来源，逗号分隔 | 空 |

商业充电/救援供应商的 JSON 契约见 `smart_roadtrip/tools/charging.py` 与 `rescue_medical.py` 模块 docstring；未配置时自动回退高德 POI。

## API 示例

### CLI

```bash
python scripts/roadtrip_cli.py plan --message "从成都到稻城亚丁玩4天，两个人，开电车"
python scripts/roadtrip_cli.py list
python scripts/roadtrip_cli.py confirm <thread_id> --approve --ack <alert_id>
python scripts/roadtrip_cli.py export <thread_id> --format pdf --output trip.pdf
```

全局参数写在子命令之前：

```bash
python scripts/roadtrip_cli.py --base-url http://127.0.0.1:8000 \
  --api-key <key> list
```

### HTTP API

创建行程（自然语言）：

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"从成都到稻城亚丁玩4天，两个人，开电车，喜欢雪山和摄影"}'
```

返回 `thread_id`、解析后的 `trip` 与 `state.pending_confirmation`。

提交确认：

```bash
curl -X POST http://127.0.0.1:8000/trips/<thread_id>/confirm \
  -H "Content-Type: application/json" \
  -d '{"decision":"approve","acknowledged_alert_ids":["<alert_id>"]}'
```

其他接口：

- `GET /trips?limit=20&offset=0`：当前 owner 的行程列表
- `GET /trips/{thread_id}`：状态/版本/待确认项
- `GET /trips/{thread_id}/report`：Markdown 路书
- `POST /trips/{thread_id}/journal`：生成游记草稿
- `GET /trips/{thread_id}/export?format=md|html|gpx|pdf`：导出
- `GET /trips/usage`：24 小时创建用量、线程总数、最近状态分布与费用汇总
- `GET /trips/audit`：当前 owner 的审计事件
- `GET /admin/usage`：管理员跨租户线程/状态/费用汇总（`X-Admin-Key`）
- `DELETE /trips/{thread_id}`：删除行程并释放配额

Python 集成可直接使用：

```python
from smart_roadtrip.client import RoadtripClient

with RoadtripClient("http://127.0.0.1:8000", api_key="<key>") as client:
    result = client.create_plan(message="从成都到稻城亚丁玩4天")
    thread_id = result["thread_id"]
```

配置 `API_KEYS` 后，所有 `/chat` 与 `/trips` 请求需要请求头：

```bash
-H "X-API-Key: <your-key>"
```

## 架构

```text
用户层      FastAPI / Streamlit
编排层      MainAgent (LangGraph) + A2A 信封 + 版本/回滚/确认
子 Agent    Route / Weather / Energy / Knowledge / Safety / Group / Report
工具与数据  高德 / 彩云 / Open-Meteo / RAG / POI；MCP server/client
```

主图：

```text
parse → dispatch → route/weather/energy/knowledge/group
      → aggregate → safety → snapshot_version → prepare_confirmation
      → await_confirmation [interrupt]
           approve → report → finalize
           reject  → replan（有额度）或 rollback
```

## 测试与部署

```bash
python -m pytest
LANGGRAPH_STRICT_MSGPACK=true python -m pytest \
  tests/test_planning_graph.py tests/test_api.py tests/test_persistence.py \
  tests/test_journal.py tests/test_export.py
```

- 测试默认关闭外部 key、Open-Meteo 与 RAG，不会访问真实服务。
- MCP server：`python smart_roadtrip/tools/mcp/server.py`
- Docker：

```bash
docker compose up -d --build
docker compose run --rm api python scripts/ingest_knowledge.py
docker compose --profile ui up -d --build
```

- CI：`.github/workflows/ci.yml`（仓库推送到 GitHub 后生效）。

## 已知限制

- 充电实时价格/排队/可用枪数、救援调度没有免费接口；当前默认只提供高德 POI 地点，配置商业供应商后由供应商返回实时字段。
- 彩云免费额度仅 3 天预报；Open-Meteo 兜底最多 16 天。
- `data/knowledge` 是示例语料，不是权威知识；支持分类子目录，替换后需重新 ingest。`data/knowledge_archive` 只做归档，不会被索引。
- SQLite checkpointer 建议单 worker；多 worker 需换并发 checkpointer。
- `TOOL_BACKEND=mcp` 的持久 gateway 有并发上限；direct 仍是默认。
- Docker/CI 文件已提供，但当前开发机没有 Docker，尚未实际构建验证。

## 安全

- 真实密钥只放 `.env`；`.env.example` 只保留空占位。
- 生产环境设置 `API_KEYS`、`RATE_LIMIT_PER_MINUTE` 与 `TENANT_MAX_THREADS_PER_DAY`。
- 需要运营报表时设置 `ADMIN_API_KEY`，通过 `X-Admin-Key` 访问 `/admin/usage`；管理员 key 不要与用户 key 混用。
- 跨用户访问统一返回 404；审计只记录 event/thread/owner/detail，不记录请求体或密钥。
- 建议通过反向代理提供 TLS、请求体大小限制与日志脱敏。
