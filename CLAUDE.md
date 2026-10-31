# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概况

项目名：**基于多智能体协作的旅行规划系统（Smart Roadtrip）**。这是一个智能自驾游多 Agent 行程规划系统。设计依据是 `docs/architecture.jpg`（根目录的微信图片为其原始副本）。当前已完成数据模型、共享状态、MainAgent 图骨架、FastAPI 接口（可选 API key/多 key 鉴权、线程归属隔离、租户配额、行程列表/用量/删除、租户审计查询、管理员跨租户用量汇总、限流、CORS、readiness、请求 ID、进程内指标与审计日志），A2A 任务/结果信封与 MCP 工具调用协议、Streamlit 原型、自然语言需求解析（LLM 可选、规则降级）、高德地图（路线/POI）、彩云天气与 Open-Meteo 免费天气、本地 RAG（FAISS + sentence-transformers）、可选的 SQLite 会话持久化、游记草稿生成（LLM 可选、模板降级）、Markdown/HTML/GPX/PDF 导出，以及 MCP stdio server/client/gateway。Agent 通过 `tools/gateway.py` 调用工具，默认直连，`TOOL_BACKEND=mcp` 时切换到持久 MCP 会话。充电/救援默认通过高德 POI 提供地点信息；配置商业供应商 base URL/key 后，EnergyAgent/SafetyAgent 会优先使用商业充电/救援适配器（实时价格/排队/可用枪数取决于供应商返回）。行程聚合会按配置参数估算能源费 + 过路费（`Itinerary.cost_estimate`），路书会展示费用章节和按同行人数均摊的金额；估算不含住宿、餐饮、门票和停车费。外部集成可直接使用 `smart_roadtrip.client.RoadtripClient` 或 `scripts/roadtrip_cli.py`。OpenAPI 文档包含中文 tags、各路由 summary 与 `ChatRequest`/`TripRequest`/`ConfirmationDecision` 示例。`README.md` 提供用户向的快速开始、配置表与 API 示例；本文件侧重开发约定、模块细节与测试说明。

## 运行环境

- Conda 环境：`smart_roadtrip`（Python 3.11.9），路径 `D:\anaconda3\envs\smart_roadtrip`
- 主要依赖：LangGraph 1.2.17、langgraph-checkpoint-sqlite 3.1.2、LangChain 1.4.7、FastAPI 0.162、Streamlit 1.66、FAISS (faiss-cpu)、sentence-transformers、reportlab、mcp 2.3、httpx

仓库没有 `pyproject.toml` / `setup.py`，包不走安装流程：所有命令必须在仓库根目录执行，靠当前工作目录把 `smart_roadtrip` 放进 `sys.path`。

常用命令（首次构建 RAG 索引会加载/下载 embedding 模型；测试可直接运行）：

```bash
conda activate smart_roadtrip
uvicorn smart_roadtrip.main:app --reload                  # FastAPI 服务
streamlit run smart_roadtrip/ui/streamlit_app.py          # 对话界面原型
python scripts/roadtrip_cli.py --help                     # HTTP 客户端 CLI
python -m pytest                                          # 全部测试
python -m pytest tests/test_imports.py::test_packages_import   # 单个测试
python scripts/ingest_knowledge.py                        # 构建 RAG 索引
python smart_roadtrip/tools/mcp/server.py                 # MCP stdio server（通常由客户端启动）
# CI 额外跑的核心子集（checkpointer 严格反序列化）
LANGGRAPH_STRICT_MSGPACK=true python -m pytest tests/test_planning_graph.py tests/test_api.py tests/test_persistence.py tests/test_journal.py tests/test_export.py
```

`requirements-dev.txt` 只额外提供 pytest；换环境后先 `pip install -r requirements-dev.txt`。仓库尚未配置 lint/formatter。

测试分类：
- `tests/test_imports.py`：冒烟测试，硬编码了需要可导入的模块清单 `MODULES`。新增模块时要同步加进这个列表，否则该模块不会被覆盖；`ui/streamlit_app.py` 由 `tests/test_streamlit_app.py` 通过 AppTest 运行覆盖，不在 `MODULES` 列表中。
- `tests/test_planning_graph.py`：编排图端到端测试，覆盖中断/批准、拒绝重排、修订次数上限回退、安全确认红线、路线失败兜底与版本回滚。
- `tests/test_request_parser.py`：自然语言规则解析、中文数字/日期、预算、偏好、缺失字段报错，以及 LLM 成功/失败降级与返回字段归一化。
- `tests/test_rag.py`：用确定性假 embedder 验证切分、FAISS 摄取/检索、缺失索引降级与 KnowledgeAgent 的真实/占位路径，不下载模型。
- `tests/test_persistence.py`：用临时 SQLite DB 验证中断状态在新建图后仍可恢复、确认后完成，以及 API 线程注册表为空时能从 checkpointer 回查。
- `tests/test_journal.py`：游记模板降级、LLM 成功/失败路径，以及从行程 state 生成游记的编排入口。
- `tests/test_export.py`：Markdown 拼接、HTML 渲染与原始 HTML 转义、GPX 轨迹/航点结构、PDF 文件头，以及无坐标/无路书时的导出错误。
- `tests/test_mcp.py`：MCP server 工具注册、stdio client 的 list/call、持久 gateway 的 MCP 后端状态/错误映射、并发调用与断线重试，以及 RouteAgent 在 MCP 模式下无 key 时的占位降级；不访问外部服务。
- `tests/test_security.py`：API key 鉴权、滑动窗口限流、`/health`/`/ready` 豁免与 readiness 检查项。
- `tests/test_tenancy.py`：多 API key 下的线程归属隔离、无 key 401、开发模式匿名访问，以及未登记归属线程在开启鉴权时被拒绝。
- `tests/test_observability.py`：`X-Request-ID` 透传、`/metrics` 计数与 `thread_created` 审计日志。
- `tests/test_quotas.py`：按 owner 统计的 24 小时行程创建配额、开发模式匿名配额、0 表示不限制、`key:limit` 单 key 配额覆盖，以及 SQLite 下配额跨重启持久化。
- `tests/test_trip_listing.py`：当前 owner 的行程列表、分页、跨 owner 隔离，以及 `/trips/usage` 的配额、线程总数、最近状态分布与费用汇总。
- `tests/test_trip_deletion.py`：删除行程会移除 owner 记录与 checkpointer 状态、释放配额；跨 owner 删除返回 404。
- `tests/test_a2a.py`：A2A 任务/结果 correlation、编排扇出信封，以及图中累计的 `a2a_responses`。
- `tests/test_audit_api.py`：租户审计事件按 owner 隔离、创建/确认/导出事件、分页，以及 SQLite 下审计事件跨重启持久化。
- `tests/test_audit_retention.py`：memory/SQLite 审计事件按保留天数清理。
- `tests/test_tool_retries.py`：工具层 5xx/429/网络错误重试、4xx 不重试。
- `tests/test_cost_estimate.py`：ICE/EV/PHEV 能源费与过路费估算、补能电价使用与占位路线标记。
- `tests/test_client.py`：Python HTTP 客户端封装、API key 头、错误映射与参数校验。
- `tests/test_admin.py`：管理员 key 校验、跨 owner 线程/状态/费用汇总、未启用时 403。
- `tests/test_logging.py`：JSON 日志格式与 `configure_logging` 格式/级别切换。
- `tests/test_openapi.py`：OpenAPI 标题/版本/标签/路径摘要与请求示例。
- `tests/test_security_headers.py`：安全响应头与请求体大小限制。
- `tests/test_report_cost.py`：路书费用章节与按人数均摊。
- `tests/test_tools_amap.py`、`tests/test_tools_caiyun.py`、`tests/test_tools_open_meteo.py`：用 `httpx.MockTransport` 模拟外部响应，覆盖地理编码、驾车路线、周边 POI、彩云/Open-Meteo 解析、缺 key/禁用、错误状态与空数据。
- `tests/test_agent_tools.py`：Route/Weather/Energy/Safety Agent 的真实工具路径、商业充电/救援路径与降级路径，以及数据来源说明。
- `tests/test_tools_charging.py`、`tests/test_tools_rescue.py`：商业供应商 HTTP 适配器契约、鉴权头、字段归一化与未配置降级。
- `tests/test_api.py`：FastAPI 接口测试，覆盖结构化/自然语言创建、确认、查询、路书与游记导出、重复线程与错误状态。
- `tests/test_streamlit_app.py`：用 `streamlit.testing.v1.AppTest` 跑界面初始渲染。

`tests/conftest.py` 会在每个测试前清空所有外部密钥，确保测试不会误连真实服务；工具测试通过 `httpx.MockTransport` 提供假响应，API 测试通过 `api/runtime.reset_runtime()` 隔离图与线程状态。测试用 `monkeypatch.setattr(config, ...)` 覆盖配置而不改 `.env`，因此业务代码应在函数调用时读取 `config.X`，不要在模块导入时缓存成模块级常量，否则测试无法切换配置。

## 架构

四层结构，依赖方向自上而下。子 Agent 之间不直接调用业务代码，统一经编排层调度，并通过 A2A 消息协作。

1. **用户层**
   - `smart_roadtrip/api/`：FastAPI 接口，已接入编排层；`runtime.py` 持有图与线程注册表（SQLite 下支持重启恢复），`schemas.py` 定义对外视图，`security.py` 提供可选 API key/多 key 鉴权与进程内限流，`ownership.py` 维护 thread_id 到 owner 的归属（memory 或同一 SQLite 文件），`observability.py` 提供请求 ID、指标、审计日志、`LOG_LEVEL`/`LOG_FORMAT` 与 JSON 日志格式，`audit_store.py` 持久化并按 owner 查询审计事件，`admin.py` 提供管理员跨租户用量汇总（需 `ADMIN_API_KEY`）
   - `smart_roadtrip/client.py`：Python HTTP 客户端，封装 health/ready/plan/confirm/list/usage/audit/export/delete；`scripts/roadtrip_cli.py` 基于它提供 CLI
   - `smart_roadtrip/ui/`：Streamlit 原型，支持自然语言/表单两种需求输入、待确认项勾选、批准/退回、Markdown/HTML/GPX/PDF 下载与游记草稿生成（直接调用编排层，不经过 HTTP）

2. **编排层** — `smart_roadtrip/orchestrator/`
   - `main_agent.py`：LangGraph 总调度，负责任务拆解、动态重排行程、版本管理、失败回退、用户确认仲裁
   - `request_parser.py`：结构化/自然语言需求解析；配置 LLM 时调用 OpenAI 兼容接口，否则或失败时回退到确定性规则
   - `journal.py`：从已完成的行程 state 生成游记草稿，调用 ReportAgent；LLM 不可用时回退模板
   - `export.py`：Markdown/独立 HTML/GPX/PDF 导出；HTML 禁用原始 HTML，GPX 使用高德折线与 POI 航点，PDF 使用 ReportLab 内置 `STSong-Light` 中文字体
   - `state.py`：跨 Agent 的共享状态
   - 关键约束：状态变更、版本推进和回滚只在这一层完成，子 Agent 只返回结果/建议

3. **子 Agent 层** — `smart_roadtrip/agents/`，职责与图一致：
   - `route_agent.py` 路线规划、多方案对比、驾驶负荷评估
   - `weather_agent.py` 天气排程与预警
   - `energy_agent.py` 电车补能规划（续航/充电桩/排队概率）
   - `knowledge_agent.py` RAG 自驾知识库检索（景点规则/装备/应急）
   - `safety_agent.py` 安全应急护航（故障/身体不适/天气路况）
   - `group_agent.py` 多人协同（偏好合并/投票/费用分摊）
   - `report_agent.py` 路书生成、游记草稿（LLM/模板）；导出格式由 `orchestrator/export.py` 负责
   - `base.py` 子 Agent 公共接口与输入输出约定

4. **工具与数据层**
   - `smart_roadtrip/tools/`：外部 API 封装，Agent 不直接发 HTTP 请求
     - `base.py` 工具公共错误（`ToolError` / `ToolUnavailableError`）、httpx JSON 辅助、超时与重试
     - `amap.py` 高德地图：地理编码、驾车路线（含折线，用于 GPX）、周边 POI（充电站/医院/药店/汽车维修），读 `AMAP_API_KEY`
     - `caiyun.py` 彩云天气：逐日预报（可选，读 `CAIYUN_API_KEY`）
     - `open_meteo.py` Open-Meteo：免费逐日预报，无需 key，最多 16 天（CC BY 4.0）
     - `weather_common.py` 天气供应商共用的 `DailyForecast` 模型
     - `charging.py` 商业充电网络适配：`CHARGING_API_BASE_URL` + `CHARGING_API_KEY`，契约见模块 docstring；未配置时返回 `None`，回退高德 POI
     - `rescue_medical.py` 商业救援/医疗适配：`RESCUE_MEDICAL_API_BASE_URL` + `RESCUE_MEDICAL_API_KEY`；未配置时回退高德 POI
     - `gateway.py` 统一工具网关：`TOOL_BACKEND=direct` 直连 tools/，`mcp` 走持久 MCP 会话；对 Agent 暴露带类型的函数
     - `mcp/server.py` MCP stdio server：暴露 `tool_status`、`amap_geocode`、`amap_route`、`amap_nearby`、`weather_forecast`、`rag_search`、`charging_nearby`、`rescue_nearby`
     - `mcp/client.py` stdio MCP 客户端：`list_tools()` / `call_tool()`，同步包装器内部使用 `asyncio.run`
     - `mcp/gateway.py` 持久化 MCP 会话：后台事件循环 + 线程安全队列 + 并发信号量（`MCP_MAX_CONCURRENCY`），避免每次调用启动新进程
   - `smart_roadtrip/rag/`：知识库摄取、向量化与检索。`ingest.py` 用 `rglob` 递归读取 `data/knowledge` 下的 `.md/.markdown/.txt` 并切分（因此支持分类子目录），`vector_store.py` 用 FAISS 内积索引持久化到 `data/vector_store`（gitignored），`retriever.py` 延迟加载索引与 embedding 模型并返回 Top-K；`embeddings.py` 封装 sentence-transformers（优先 `local_files_only=True` 使用本地缓存，缺失时才联网下载），`paths.py` 统一目录。`data/knowledge` 内是可直接替换的示例语料，当前 11 份文档分布在 `activities/destinations/energy/equipment/family/regulations/routes/safety` 8 个分类目录；旧版 9 份单目录语料已移到 `data/knowledge_archive/`，不会被索引。
   - `smart_roadtrip/schemas/`：Pydantic 数据模型
   - `smart_roadtrip/config.py`：从 `.env` 统一读取配置

依赖方向固定为「用户层 → 编排层 → 子 Agent → 工具/数据」。子 Agent 之间、Agent 与外部 HTTP API 之间都不直接耦合：前者走 A2A，后者走 `tools/` + MCP。Agent 现在统一通过 `tools/gateway.py` 调用工具：默认 `direct` 直连，`TOOL_BACKEND=mcp` 时走 `tools/mcp/gateway.py` 的持久 stdio 会话。MCP 使用官方 SDK 2.x（`MCPServer`）。持久 gateway 以 `MCP_MAX_CONCURRENCY`（默认 8）为上限并发执行工具调用；会话不可用时 `tools/gateway` 会重启 gateway 并重试一次。

LLM 走 OpenAI 兼容接口（依赖里有 `langchain-openai`），由 `config.py` 暴露的 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 配置，不写死服务商。RAG 由 `RAG_ENABLED` / `RAG_EMBEDDING_MODEL` / `RAG_TOP_K` / `RAG_CHUNK_CHARS` / `RAG_CHUNK_OVERLAP` 配置；会话持久化由 `CHECKPOINTER_BACKEND`（`memory` / `sqlite`）与 `CHECKPOINT_DB_PATH` 配置；工具后端由 `TOOL_BACKEND`（`direct` / `mcp`）与 `MCP_MAX_CONCURRENCY` 配置；商业充电/救援由 `CHARGING_API_BASE_URL` / `CHARGING_API_KEY` 与 `RESCUE_MEDICAL_API_BASE_URL` / `RESCUE_MEDICAL_API_KEY` 配置（未配置时回退高德 POI）。API 安全由 `API_KEY` / `API_KEYS` / `API_KEY_HEADER` / `ADMIN_API_KEY` / `ADMIN_API_KEY_HEADER` / `RATE_LIMIT_PER_MINUTE` / `TENANT_MAX_THREADS_PER_DAY` / `CORS_ORIGINS` 配置；审计保留由 `AUDIT_RETENTION_DAYS` 配置；工具 HTTP 超时/重试由 `TOOL_TIMEOUT_SECONDS` / `TOOL_MAX_RETRIES` / `TOOL_RETRY_BACKOFF_SECONDS` 配置；费用估算由 `FUEL_PRICE_CNY_PER_L` / `FUEL_CONSUMPTION_L_PER_100KM` / `ELECTRICITY_PRICE_CNY_PER_KWH` / `EV_CONSUMPTION_KWH_PER_100KM` / `PHEV_FUEL_SHARE` 配置；日志由 `LOG_LEVEL` / `LOG_FORMAT`（text/json）配置；请求体上限由 `MAX_REQUEST_BYTES` 配置。`.env` 从 `.env.example` 复制，变量名以其为准；`.env.example` 只保留空占位，真实密钥只写入被 gitignore 的 `.env`。`data/vector_store` 与 `data/*.sqlite*` 的产物已被 gitignore。

## 编排图运行方式

`main_agent.py` 已实现可运行图：

```
START → parse → dispatch ─Send 扇出→ route / weather / knowledge / group
                                     （energy 仅当 request.needs_charging 时加入）
      → aggregate → safety → snapshot_version → prepare_confirmation
      → await_confirmation [interrupt]
           approve → report → finalize → END
           reject + 有修订额度 → replan → dispatch
           reject + 额度用尽   → rollback → END
```

- 用 `build_graph(*, checkpointer=None, max_revisions=2, backend=None)` 构建图；不传 checkpointer 时按 `CHECKPOINTER_BACKEND`（`memory` / `sqlite`）创建带类型白名单的 checkpointer。测试默认 `memory`；生产可复用同一图，靠唯一 `thread_id` 隔离会话。
- 扇出 Agent 由 `select_agents()` 决定：固定 route/weather/knowledge/group，`request.needs_charging` 为真时才追加 energy；`_FANOUT_AGENTS` 是节点注册表。safety/report 依赖上游结果，串行执行。
- `aggregate` 只取 `route_options[0]` 生成行程，每日活动目前是按天数拼的确定性模板（首日抵达、末日返程、中间按 `preferences.interests` 轮换），POI/景点尚未参与日程编排；能源费与过路费在 `_estimate_cost()` 中估算。改日程生成只需动 `_build_itinerary()`，不要在子 Agent 里改状态。
- `CHECKPOINTER_BACKEND=sqlite` 时状态持久化到 `CHECKPOINT_DB_PATH`，进程重启后可用同一 `thread_id` 恢复待确认/已完成行程。SQLite 连接使用 `check_same_thread=False`，由 `close_graph()` / `reset_default_graph()` / `api.runtime.reset_runtime()` 关闭。
- 测试应每次新建图；生产中可以复用同一图，但不要跨会话共享同一个 `thread_id`。
- 需求输入：`start_planning` 接受 `TripRequest`、字典或字符串。字符串由 `request_parser.parse_request_text()` 处理；配置了 `LLM_API_KEY` + `LLM_MODEL` 时使用 `ChatOpenAI`（20 秒超时、不重试），调用/校验失败自动回退规则解析。规则解析覆盖「从X到Y」「N天」「N人」「电车/混动/油车」「预算N元/万」、中文数字、相对日期与常见兴趣关键词；缺少出发地/目的地会抛出 `RequestParseError`（API 映射为 422）。
- LLM 配置由 `config.py` 在进程启动时读取；新增 `.env` 或修改 key 后需重启服务。
- `start_planning(request, thread_id, graph=...)` 返回的 state 若含 `__interrupt__`，表示等待确认；用 `resume_planning({"decision": "approve" | "reject", "feedback": ..., "acknowledged_alert_ids": [...]}, thread_id, graph=...)` 恢复。若向 `start_planning` 传了自定义图，`resume_planning` 必须复用同一个图实例，否则中断状态会丢失。
- `interrupt()` 依赖 checkpointer，且必须提供 `thread_id`。用户在 `resume` 中批准但未确认任何 `requires_user_confirmation=True` 的安全提醒时，编排层会把批准转为拒绝并触发重排（安全红线）。
- 每个子 Agent 的结果统一放在 `state["agent_results"]`（按 Agent 名索引），不再维护各 Agent 的独立 state 字段。`a2a_responses` 保存 A2A 结果信封历史（只含 summary/status/is_placeholder，不含完整 payload）。`versions` 是不可变快照列表，`current_version_id` 指向最新候选，`confirmed_version_id` 指向最后批准的版本；批准不会重复追加版本。
- 被 `interrupt()` 的节点恢复时会从头重跑，因此确认摘要必须在 `prepare_confirmation` 构造，`await_confirmation` 只做暂停与状态更新。
- checkpointer 的类型白名单由 `_allowed_msgpack_types()` 自动收集 `schemas.trip` 与 `protocols.a2a` 中的全部模型/枚举；新增这两处的领域类型不需要手工维护白名单。
- `RouteAgent` / `WeatherAgent` / `EnergyAgent` / `SafetyAgent` 优先调用 `tools/`。未配置 key（`ToolUnavailableError`）、外部服务错误（`ToolError`）或其他异常都会降级为占位结果，并用 `AgentResult.is_placeholder` 标记来源；Safety/Report 依据该标记与 `data_sources` 生成数据来源说明。
- `WeatherAgent` 先高德地理编码目的地；配置彩云 key 时优先彩云，否则或覆盖不足时自动使用无需 key 的 Open-Meteo（最多 16 天）。两者都不覆盖行程日期时降级占位。彩云降水概率按天气现象估算，Open-Meteo 使用 `precipitation_probability_max`；`OPEN_METEO_ENABLED=0` 可关闭。
- `EnergyAgent` 通过高德 POI 类型 `011100` 搜索目的地周边充电站，只提供站点名称/地址/坐标；实时价格、排队与可用枪数保持 `None`，并在 `notes` 中说明。未找到站点时降级占位。
- `SafetyAgent` 通过高德 POI 搜索医院/药店/汽车维修，写入 `AgentResult.rescue_resources`；这只代表地点信息，实时急诊能力与救援调度未接入。
- `AgentResult.data_sources` 记录来源（`amap_driving` / `amap_geocode` / `amap_poi` / `caiyun` / `open_meteo` / `rag_faiss`）。ReportAgent 输出「数据来源」，使用 Open-Meteo 时附 CC BY 4.0 署名。
- `KnowledgeAgent` 用目的地 + 兴趣 + 「自驾 景点规则 装备 应急」检索本地 FAISS 索引，返回 `RAG_TOP_K` 条片段；索引缺失、模型加载失败或未命中时返回占位片段。`RAG_ENABLED=0` 可关闭检索。
- 修改 `data/knowledge` 或切换 `RAG_EMBEDDING_MODEL` 后需重新运行 `python scripts/ingest_knowledge.py`；索引向量维度必须与当前模型一致，否则检索会安全降级。当前 11 份示例文档在默认切分配置下已重建为 22 个片段。
- embedding 模型优先从本地缓存加载，避免每次启动都检查 Hugging Face Hub；本地无缓存时才联网下载。
- 测试通过 `tests/conftest.py` 清空密钥、关闭 Open-Meteo 与 RAG；工具测试显式传入 `httpx.MockTransport` client，RAG 测试使用假 embedder，避免真实网络与模型下载。
- 工具层 HTTP 默认超时 10 秒、最多重试 2 次（网络错误 / 429 / 5xx，指数退避），其他 4xx 不重试；由 `TOOL_TIMEOUT_SECONDS` / `TOOL_MAX_RETRIES` / `TOOL_RETRY_BACKOFF_SECONDS` 配置。
- 工具层错误信息刻意不拼接完整请求 URL，避免 API key 写入日志。

## HTTP 接口

路由在 `api/chat.py`、`api/trips.py` 与 `api/admin.py`，由 `main.py` 统一挂载；`/health`、`/ready`、`/metrics` 直接定义在 `main.py`：

- `POST /chat`：body 提供 `trip`（结构化 `TripRequest`）或 `message`（自然语言）之一：`{"thread_id": "可选", "trip": {...}}` 或 `{"thread_id": "可选", "message": "..."}`。返回 `thread_id`、解析后的 `trip`、`awaiting_confirmation` 与 `TripStateView`。缺省 `thread_id` 由服务端生成；重复的 `thread_id` 返回 409；自然语言无法解析返回 422。
- `GET /trips?limit=20&offset=0`：列出当前 owner 的行程（按创建时间倒序），返回 `thread_id`、创建时间、状态、出发地/目的地、天数和版本指针。只返回归属当前 key 的线程。
- `GET /trips/usage`：返回当前 owner 最近 24 小时的创建数、配额、剩余额度，以及 `total_threads`、最近 100 个线程的 `recent_by_status` / `recent_estimated_cost_cny` 和 `last_created_at`；`limit=0` 时 `remaining` 为 `null`。
- `GET /trips/audit?limit=50&offset=0`：返回当前 owner 的审计事件（event/thread_id/detail/created_at），按时间倒序；memory 模式最多保留 5000 条，SQLite 模式持久化。`AUDIT_RETENTION_DAYS` 默认 30 天，0 表示永久保留；每 100 次写入会机会式清理过期事件。
- `DELETE /trips/{thread_id}`：删除当前 owner 的行程归属与 checkpointer 状态，成功返回 204。删除后该线程不可再查询，并释放对应的创建配额；跨 owner 删除返回 404。
- `GET /trips/{thread_id}`：查询状态、轮次、版本、行程、待确认项、路书与错误。
- `POST /trips/{thread_id}/confirm`：body 为 `ConfirmationDecision`（`decision: "approve" | "reject"`、`feedback`、`acknowledged_alert_ids`）。仅当状态为 `awaiting_confirmation` 时可用，否则返回 409。
- `GET /trips/{thread_id}/report`：返回已确认行程的 `ReportResponse{thread_id, version_id, markdown}`；路书未生成时返回 404。
- `POST /trips/{thread_id}/journal`：基于已完成的行程生成游记草稿，返回 `JournalResponse{thread_id, version_id, markdown, generated_by}`；`generated_by` 为 `llm` 或 `template`。行程未完成返回 409。游记不写入行程状态，可重复生成。
- `GET /trips/{thread_id}/export?format=md|html|gpx|pdf&include_journal=false`：以附件形式导出已确认行程。`include_journal=true` 会把游记草稿拼入 Markdown/HTML/PDF；GPX 优先使用高德路线折线，没有折线时退化为充电站/医疗维修航点，二者都没有则返回 409。PDF 使用内置中文字体，不依赖系统字体文件。
- `GET /health`：健康检查。
- `GET /ready`：readiness 检查，返回 checkpointer 后端、数据源/RAG/LLM 配置状态与 SQLite 文件是否存在；不返回密钥，公开访问。
- `GET /admin/usage?limit=100`：管理员跨租户用量汇总，返回租户数、线程总数、各 owner 的线程数/状态分布/费用估算/最近创建时间。需要 `X-Admin-Key`；owner 为 key 哈希或 anonymous，不暴露原始 key 或 thread_id。未配置 `ADMIN_API_KEY` 时返回 403。
- `GET /metrics`：进程内累计请求数、状态码分布、路由模板计数与平均耗时；不包含密钥或请求体，公开访问。
- 所有响应都会带 `X-Request-ID`；客户端可传入自定义 `X-Request-ID`，服务端会原样返回。

游记优先使用已配置的 LLM；未配置或调用失败时自动回退模板，不会因 LLM 故障导致请求失败。

`api/runtime.py` 持有模块级图；默认 `MemorySaver`，配置 `CHECKPOINTER_BACKEND=sqlite` 时使用持久化 checkpointer，按 `thread_id` 隔离会话。`thread_exists()` 在内存注册表未命中时会回查 checkpointer，因此服务重启后仍能通过 `GET /trips/{thread_id}` 恢复；测试用 `reset_runtime()` 清空并关闭连接。`TripStateView` 刻意不暴露 `agent_results`，只给客户端稳定的行程视图。

## 部署与安全

- `API_KEY` / `API_KEYS` 都为空时是开发模式，`/chat` 与 `/trips` 不鉴权；生产环境建议用 `API_KEYS` 配置逗号分隔的多用户 key，客户端通过 `X-API-Key`（可用 `API_KEY_HEADER` 改名）传递。`API_KEYS` 支持 `key-a:10` 形式为该 key 单独设置最近 24 小时创建上限。
- 管理员接口通过 `ADMIN_API_KEY` + `X-Admin-Key` 启用；未配置时 `/admin/*` 返回 403。管理员 key 不要与普通用户 key 混用。
- 每个 API key 的哈希作为 owner；`POST /chat` 创建线程时登记归属，`/trips/{thread_id}` 的查询、确认、游记、导出都会校验归属。跨用户访问统一返回 404，避免泄露线程是否存在。
- 开发模式下 owner 为 `anonymous`；开启鉴权后，没有归属记录的旧线程会被拒绝访问，需要重新创建。
- `TENANT_MAX_THREADS_PER_DAY` 是默认配额（`0` 表示不限制）；`API_KEYS=key-a:10` 可为单个 key 覆盖。超限返回 429。配额按 API key 哈希区分，开发模式下所有匿名请求共享一个配额。`CHECKPOINTER_BACKEND=sqlite` 时计数来自同一 SQLite 的 `thread_owners.created_at`，服务重启后仍然有效。删除行程会同时移除归属记录与 checkpointer 状态，因此会释放配额。
- `RATE_LIMIT_PER_MINUTE` 是进程内滑动窗口，默认 60；多 worker 部署时每个进程独立计数，建议在反向代理层再做全局限流。
- `CORS_ORIGINS` 为空时不添加 CORS 中间件；多个来源用逗号分隔。
- `/health`、`/ready` 与 `/metrics` 不需要鉴权；`/ready` 只返回布尔状态与后端类型，`/metrics` 不包含密钥或请求体。
- 请求日志只记录 method、路由模板、状态码、耗时与 request id，不记录 query、请求体或 API key；审计事件同时写入 `smart_roadtrip.audit` logger 与 `audit_events` 存储，包含 event/thread/owner/detail（owner 为 key 哈希或 anonymous），并可通过 `/trips/audit` 按 owner 查询。
- `/metrics` 是进程内累计值，多 worker 部署时各进程独立；需要聚合指标时接入 Prometheus/OpenTelemetry 或反向代理日志。
- `LOG_FORMAT=json` 时 `smart_roadtrip` 包日志输出单行 JSON（ts/level/logger/message），适合 ELK/Loki；默认 text 格式。
- 所有响应添加 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`；HTTPS 下额外加 HSTS。`/chat`、`/trips`、`/admin` 的请求体超过 `MAX_REQUEST_BYTES` 返回 413。
- `CHECKPOINTER_BACKEND=sqlite` 时建议 uvicorn 单 worker（`--workers 1`），或换成支持并发的 checkpointer；SQLite 文件不要放进版本库。
- 建议通过反向代理（Nginx/Caddy）提供 TLS、请求体大小限制与日志脱敏。

### Docker

仓库提供 `Dockerfile`、`.dockerignore` 与 `docker-compose.yml`：

```bash
cp .env.example .env          # 填写密钥；.env 不会被打进镜像
docker compose up -d --build  # API: http://localhost:8000
docker compose run --rm api python scripts/ingest_knowledge.py  # 首次构建 RAG 索引（可选）
docker compose --profile ui up -d --build                       # 可选 Streamlit: :8501
```

- API 服务使用 `CHECKPOINTER_BACKEND=sqlite`，`data/` 挂载到宿主机，SQLite 与 RAG 索引都持久化在宿主机。
- `hf_cache` 命名卷缓存 sentence-transformers 模型；首次构建索引/检索需要网络。
- Dockerfile 先安装 CPU 版 torch，避免默认 CUDA wheel 让镜像达到数 GB。
- 容器内 API 以 UID 10001 运行；Linux 上请确保 `./data` 可写：`sudo chown -R 10001:10001 data`（Windows Docker Desktop 通常无需处理）。
- Compose 中 API 固定单 worker；需要多 worker 时应替换 SQLite checkpointer，并考虑把限流移到反向代理。
- 当前开发环境没有安装 Docker，Docker 文件未在本机实际构建验证；请在有 Docker 的机器上先执行 `docker compose config` 与构建。

### CI

- `.github/workflows/ci.yml`：Ubuntu + Python 3.11，先装 CPU 版 torch，再安装 `requirements-dev.txt`，运行全量 `pytest` 与 `LANGGRAPH_STRICT_MSGPACK=true` 的核心子集。
- Docker 镜像构建未放进 CI（torch/模型依赖体积大，构建时间长）。
- 仓库已是 git 仓库（`main` 分支）；推送到 GitHub 后该 workflow 才会实际运行。

## 协议与保障

- **A2A**（`protocols/a2a.py`）管理 Agent 之间的协作：编排层用 `A2AMessage` 委派任务，子 Agent 用 `A2AResponse` 返回结果，`correlation_id` 关联请求/响应；结果信封累计在 `state["a2a_responses"]`。**MCP**（`tools/mcp/`）管理 Agent 到工具的调用，两者正交。MCP server/client/gateway 已实现，Agent 通过 `tools/gateway.py` 在 direct 与 mcp 后端之间切换。
- 三大保障（架构红线）：容错回退、人在回路确认、建议与决策红线

## 扩展点

- **新增子 Agent**：`schemas/trip.py` 的 `AgentName` 加枚举 → 在 `agents/` 继承 `BaseAgent` 实现 `_run()`（只返回 `AgentResult`，异常由基类兜底）→ 并行 Agent 注册进 `main_agent._FANOUT_AGENTS` 并在 `select_agents()` 选入；依赖上游结果的串行 Agent 需在 `build_graph()` 加节点与边 → 模块加入 `tests/test_imports.py::MODULES`。
- **新增工具**：实现在 `tools/`，复用 `tools/base.py` 的 httpx JSON 辅助与超时/重试，错误信息不拼接 URL → 在 `tools/gateway.py` 同时补齐 direct 与 mcp 两个分支 → 在 `tools/mcp/server.py` 注册 MCP 工具 → 模块加入 `tests/test_imports.py::MODULES`。
- **新增配置**：写入 `config.py`（给默认值与边界约束）并同步 `.env.example`；`.env` 在模块导入时读取，改完需重启进程。
- **新增导出格式**：在 `orchestrator/export.py` 增加渲染函数，接入 `api/trips.py` 的 `format` 字面量、`client.py`/CLI 与 Streamlit 下载入口。

## 关键约定

- 安全/应急类建议必须经用户确认；Agent 只产出建议，不替用户做决策
- 面向用户的文案与行程输出使用中文
- 新增外部数据源统一封装进 `tools/` 并以 MCP 工具暴露；知识类语料补进 `data/knowledge`
- 免费 POI/天气只作为地点或预报信息，不得把估算值伪装成实时价格、排队、可用性或救援调度；输出中必须标注数据来源
- 配置项经 `config.py` 读取，变量名以 `.env.example` 为准
