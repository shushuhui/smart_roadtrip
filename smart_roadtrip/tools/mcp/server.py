"""MCP server：把基于多智能体协作的旅行规划系统的工具层暴露给外部 MCP 客户端。

运行方式（stdio，通常由 MCP 客户端启动）：
    python smart_roadtrip/tools/mcp/server.py

说明：当前 Agent 仍直接调用 tools/ 中的函数；该 server 提供标准的 MCP
接口，便于后续把 Agent 到工具的调用迁移到 MCP。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 直接执行脚本时确保仓库根目录在 sys.path 中。
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from smart_roadtrip import config  # noqa: E402
from smart_roadtrip.rag import retriever  # noqa: E402
from smart_roadtrip.schemas.trip import GeoPoint  # noqa: E402
from smart_roadtrip.tools import (  # noqa: E402
    amap,
    caiyun,
    charging,
    open_meteo,
    rescue_medical,
)
from smart_roadtrip.tools.base import ToolError  # noqa: E402

mcp = MCPServer(
    "smart-roadtrip-tools",
    instructions=(
        "自驾游行程规划工具集：高德地图路线/POI、彩云/Open-Meteo 天气、"
        "本地 RAG 知识库。调用方需自行处理安全提示与用户确认。"
    ),
)


@mcp.tool()
def tool_status() -> dict:
    """返回当前配置与本地 RAG 索引状态（不访问外部服务）。"""

    return {
        "amap_configured": amap.is_configured(),
        "caiyun_configured": caiyun.is_configured(),
        "open_meteo_enabled": config.OPEN_METEO_ENABLED,
        "rag_enabled": config.RAG_ENABLED,
        "rag_index_available": retriever.is_available(),
        "commercial_charging_configured": charging.is_configured(),
        "commercial_rescue_configured": rescue_medical.is_configured(),
    }


@mcp.tool()
def amap_geocode(address: str) -> dict:
    """把地名解析为经纬度。"""

    return amap.geocode(address).model_dump(mode="json")


@mcp.tool()
def amap_route(origin: str, destination: str) -> list[dict]:
    """规划起点到终点的驾车路线（含折线）。"""

    return [
        route.model_dump(mode="json")
        for route in amap.route_plan(origin, destination)
    ]


@mcp.tool()
def amap_nearby(
    latitude: float,
    longitude: float,
    keywords: str | None = None,
    types: str | None = None,
    radius_m: int = 50_000,
    limit: int = 10,
) -> list[dict]:
    """按中心点搜索周边 POI（充电站、医院、药店、汽车维修等）。"""

    point = GeoPoint(lng=longitude, lat=latitude)
    return [
        poi.model_dump(mode="json")
        for poi in amap.search_nearby(
            point,
            keywords=keywords,
            types=types,
            radius_m=radius_m,
            limit=limit,
        )
    ]


@mcp.tool()
def weather_forecast(
    latitude: float,
    longitude: float,
    provider: str = "auto",
    days: int = 16,
) -> list[dict]:
    """获取逐日天气预报；provider 可为 auto/caiyun/open_meteo。"""

    point = GeoPoint(lng=longitude, lat=latitude)
    selected = provider.strip().lower()

    if selected == "caiyun":
        return [
            item.model_dump(mode="json")
            for item in caiyun.daily_weather(point, days=days)
        ]
    if selected == "open_meteo":
        return [
            item.model_dump(mode="json")
            for item in open_meteo.daily_weather(point, days=days)
        ]
    if selected != "auto":
        raise ValueError("provider 只能是 auto / caiyun / open_meteo")

    if caiyun.is_configured():
        try:
            return [
                item.model_dump(mode="json")
                for item in caiyun.daily_weather(point, days=days)
            ]
        except ToolError:
            pass

    if config.OPEN_METEO_ENABLED:
        return [
            item.model_dump(mode="json")
            for item in open_meteo.daily_weather(point, days=days)
        ]

    raise ToolError("没有可用的天气数据源（彩云未配置且 Open-Meteo 已关闭）。")


@mcp.tool()
def charging_nearby(
    latitude: float,
    longitude: float,
    radius_km: float = 50.0,
    limit: int = 10,
) -> list[dict]:
    """商业充电网络周边查询；未配置供应商时返回错误。"""

    provider = charging.get_provider()
    if provider is None:
        raise ToolError("未配置商业充电网络。")
    point = GeoPoint(lng=longitude, lat=latitude)
    return [
        station.model_dump(mode="json")
        for station in provider.nearby(
            point, radius_km=radius_km, limit=limit
        )
    ]


@mcp.tool()
def rescue_nearby(
    latitude: float,
    longitude: float,
    radius_km: float = 50.0,
    limit: int = 9,
) -> list[dict]:
    """商业救援/医疗资源周边查询；未配置供应商时返回错误。"""

    provider = rescue_medical.get_provider()
    if provider is None:
        raise ToolError("未配置商业救援网络。")
    point = GeoPoint(lng=longitude, lat=latitude)
    return [
        resource.model_dump(mode="json")
        for resource in provider.nearby(
            point, radius_km=radius_km, limit=limit
        )
    ]


@mcp.tool()
def rag_search(query: str, top_k: int = 5) -> list[dict]:
    """检索本地自驾知识库。"""

    if not config.RAG_ENABLED:
        raise ToolError("RAG 已在当前配置中关闭。")
    return [
        snippet.model_dump(mode="json")
        for snippet in retriever.retrieve(query, top_k=top_k)
    ]


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
