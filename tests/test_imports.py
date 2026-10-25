"""冒烟测试：确保脚手架包与模块可导入。"""

import importlib

MODULES = [
    "smart_roadtrip",
    "smart_roadtrip.client",
    "smart_roadtrip.config",
    "smart_roadtrip.main",
    "smart_roadtrip.orchestrator.export",
    "smart_roadtrip.orchestrator.journal",
    "smart_roadtrip.orchestrator.main_agent",
    "smart_roadtrip.orchestrator.request_parser",
    "smart_roadtrip.orchestrator.state",
    "smart_roadtrip.agents.base",
    "smart_roadtrip.agents.route_agent",
    "smart_roadtrip.agents.weather_agent",
    "smart_roadtrip.agents.energy_agent",
    "smart_roadtrip.agents.knowledge_agent",
    "smart_roadtrip.agents.safety_agent",
    "smart_roadtrip.agents.group_agent",
    "smart_roadtrip.agents.report_agent",
    "smart_roadtrip.tools.amap",
    "smart_roadtrip.tools.base",
    "smart_roadtrip.tools.caiyun",
    "smart_roadtrip.tools.charging",
    "smart_roadtrip.tools.gateway",
    "smart_roadtrip.tools.open_meteo",
    "smart_roadtrip.tools.rescue_medical",
    "smart_roadtrip.tools.weather_common",
    "smart_roadtrip.tools.mcp.client",
    "smart_roadtrip.tools.mcp.gateway",
    "smart_roadtrip.tools.mcp.server",
    "smart_roadtrip.rag.embeddings",
    "smart_roadtrip.rag.ingest",
    "smart_roadtrip.rag.paths",
    "smart_roadtrip.rag.retriever",
    "smart_roadtrip.rag.vector_store",
    "smart_roadtrip.protocols.a2a",
    "smart_roadtrip.schemas.trip",
    "smart_roadtrip.api.admin",
    "smart_roadtrip.api.audit_store",
    "smart_roadtrip.api.chat",
    "smart_roadtrip.api.observability",
    "smart_roadtrip.api.ownership",
    "smart_roadtrip.api.runtime",
    "smart_roadtrip.api.schemas",
    "smart_roadtrip.api.security",
    "smart_roadtrip.api.trips",
    "smart_roadtrip.ui",
]


def test_packages_import() -> None:
    for name in MODULES:
        importlib.import_module(name)
