"""ReportAgent：路书生成、行程导出、游记生成。

当前实现中文 Markdown 路书渲染与游记草稿生成（LLM 可选，失败回退模板），
不生成 PDF/GPX 等导出文件。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from smart_roadtrip import config
from smart_roadtrip.agents.base import AgentContext, BaseAgent, placeholder_result
from smart_roadtrip.schemas.trip import AgentName, AgentResult

logger = logging.getLogger(__name__)

_SOURCE_LABELS = {
    "placeholder": "占位数据",
    "amap_driving": "高德地图路线",
    "amap_geocode": "高德地理编码",
    "amap_poi": "高德 POI",
    "caiyun": "彩云天气",
    "open_meteo": "Open-Meteo",
    "rag_faiss": "本地 RAG 索引",
    "commercial_charging": "商业充电网络",
    "commercial_rescue": "商业救援网络",
}


class ReportAgent(BaseAgent):
    name = AgentName.REPORT

    def _run(self, ctx: AgentContext) -> AgentResult:
        markdown = _render_markdown(ctx)
        return placeholder_result(
            AgentName.REPORT,
            "已生成中文路书；具体数据来源见文末标注。",
            round=ctx.round,
            report_markdown=markdown,
        )

    def generate_travelogue(self, ctx: AgentContext) -> tuple[str, str]:
        """生成游记草稿，返回 ``(markdown, generated_by)``。

        ``generated_by`` 为 ``llm`` 或 ``template``；LLM 未配置/调用失败时
        自动使用模板，不抛异常。
        """

        roadbook = _render_markdown(ctx)
        generated = _travelogue_with_llm(roadbook)
        if generated:
            return generated, "llm"
        return _travelogue_template(ctx), "template"


def _render_markdown(ctx: AgentContext) -> str:
    request = ctx.request
    itinerary = ctx.itinerary
    if itinerary is None:
        return (
            f"# {request.origin} → {request.destination} 自驾路书\n\n"
            "> 当前没有可用行程，无法生成完整路书。\n"
        )

    lines: list[str] = [
        f"# {itinerary.title}",
        "",
        _source_note(ctx),
        "",
        "## 行程概览",
        f"- 出发地：{request.origin}",
        f"- 目的地：{request.destination}",
        f"- 出行日期：{request.start_date.isoformat()} 起 {request.days} 天",
        f"- 总里程：约 {itinerary.total_distance_km:.1f} 公里",
        f"- 总驾驶时长：约 {itinerary.total_drive_hours:.1f} 小时",
        f"- 同行人：{'、'.join(t.name for t in request.travelers)}",
        "",
    ]

    cost = itinerary.cost_estimate
    if cost is not None:
        lines.extend(
            [
                "## 费用估算",
                f"- 能源：约 {cost.energy_cost_cny:.0f} 元",
                f"- 过路费：约 {cost.tolls_cny:.0f} 元",
                f"- 合计：约 {cost.total_cny:.0f} 元",
            ]
        )
        traveler_count = max(1, len(request.travelers))
        lines.append(
            f"- 按 {traveler_count} 人均摊：约 "
            f"{cost.total_cny / traveler_count:.0f} 元/人"
            "（仅能源 + 过路费）"
        )
        for assumption in cost.assumptions:
            lines.append(f"- {assumption}")
        lines.append("")

    lines.extend(["## 每日安排", ""])

    for day in itinerary.days:
        lines.extend(
            [
                f"### 第 {day.day_index} 天 · {day.date.isoformat()} · {day.title}",
                "",
            ]
        )
        for activity in day.activities:
            time_prefix = f"{activity.start_time} " if activity.start_time else ""
            lines.append(f"- {time_prefix}{activity.title}（{activity.kind}）")
        if day.driving_km:
            lines.append(f"- 当日驾车约 {day.driving_km:.1f} 公里")
        if day.notes:
            lines.append(f"- 备注：{day.notes}")
        lines.append("")

    lines.extend(["## 路线方案", ""])
    route_result = ctx.upstream.get(AgentName.ROUTE.value)
    if route_result and route_result.route_options:
        lines.append("| 方案 | 里程 | 时长 | 过路费 | 驾驶负荷 |")
        lines.append("| --- | ---: | ---: | ---: | --- |")
        for option in route_result.route_options:
            lines.append(
                f"| {option.name} | {option.distance_km:.1f} km | "
                f"{option.duration_min} 分钟 | {option.tolls_cny:.1f} 元 | "
                f"{option.driving_load.value} |"
            )
    else:
        lines.append("- 暂无路线方案。")
    lines.append("")

    lines.extend(["## 天气", ""])
    weather_result = ctx.upstream.get(AgentName.WEATHER.value)
    if weather_result and weather_result.weather_windows:
        for window in weather_result.weather_windows:
            warning = f"；{window.warning}" if window.warning else ""
            lines.append(
                f"- 第 {window.day_index} 天：{window.condition}，"
                f"{window.temp_low_c:.0f}–{window.temp_high_c:.0f}℃，"
                f"降水概率 {window.precipitation_probability:.0%}{warning}"
            )
    else:
        lines.append("- 暂无天气数据。")
    lines.append("")

    lines.extend(["## 补能与住宿", ""])
    energy_result = ctx.upstream.get(AgentName.ENERGY.value)
    if energy_result and energy_result.charging_stops:
        for stop in energy_result.charging_stops:
            queue = (
                f"排队概率 {stop.queue_probability:.0%}"
                if stop.queue_probability is not None
                else "排队概率未知"
            )
            price = (
                f"电价 {stop.price_per_kwh:.2f} 元/度"
                if stop.price_per_kwh is not None
                else "电价未知"
            )
            source = _SOURCE_LABELS.get(stop.source, stop.source)
            extra = [f"来源：{source}"]
            if stop.notes:
                extra.append(stop.notes)
            lines.append(
                f"- 第 {stop.day_index} 天：{stop.name}，"
                f"充电约 {stop.charge_minutes} 分钟，{queue}，{price}；"
                f"{'；'.join(extra)}"
            )
    elif request.needs_charging:
        lines.append("- 暂无补能点。")
    else:
        lines.append("- 燃油车，无需补能规划。")
    lines.append("")

    lines.extend(["## 安全提示（需逐项确认）", ""])
    safety_result = ctx.upstream.get(AgentName.SAFETY.value)
    if safety_result and safety_result.safety_alerts:
        for alert in safety_result.safety_alerts:
            lines.append(
                f"- **[{alert.severity.value}] {alert.category}**：{alert.message} "
                f"建议：{alert.recommendation}"
            )
    else:
        lines.append("- 暂无安全提醒。")
    lines.append("")

    lines.extend(["## 附近医疗与维修资源", ""])
    if safety_result and safety_result.rescue_resources:
        for resource in safety_result.rescue_resources:
            phone = resource.phone or "电话未知"
            address = resource.address or "地址未知"
            note = f"；{resource.notes}" if resource.notes else ""
            source = _SOURCE_LABELS.get(resource.source, resource.source)
            lines.append(
                f"- [{resource.category}] {resource.name}；{address}；{phone}"
                f"（来源：{source}{note}）"
            )
    else:
        lines.append("- 暂无；救援/医疗实时数据未接入。")
    lines.append("")

    lines.extend(["## 知识库摘录", ""])
    knowledge_result = ctx.upstream.get(AgentName.KNOWLEDGE.value)
    if knowledge_result and knowledge_result.knowledge_snippets:
        for snippet in knowledge_result.knowledge_snippets:
            lines.append(f"- **{snippet.title}**：{snippet.content}（来源：{snippet.source}）")
    else:
        lines.append("- 暂无知识库内容。")
    lines.append("")

    lines.extend(["## 费用分摊", ""])
    group_result = ctx.upstream.get(AgentName.GROUP.value)
    if group_result and group_result.group_plan and group_result.group_plan.cost_split:
        for name, amount in group_result.group_plan.cost_split.items():
            lines.append(f"- {name}：{amount:.2f} 元")
        if group_result.group_plan.notes:
            lines.append(f"- 说明：{group_result.group_plan.notes}")
    else:
        lines.append("- 暂未计算费用分摊。")
    lines.append("")

    lines.extend(["## 数据来源", ""])
    lines.extend(_data_source_lines(ctx) or ["- 暂无。"])
    lines.append("")

    return "\n".join(lines)


def _source_note(ctx: AgentContext) -> str:
    real_sources: list[str] = []
    placeholder_sources: list[str] = []
    for label, agent_name in (
        ("路线", AgentName.ROUTE),
        ("天气", AgentName.WEATHER),
        ("补能", AgentName.ENERGY),
    ):
        result = ctx.upstream.get(agent_name.value)
        if result is None:
            continue
        if result.is_placeholder:
            placeholder_sources.append(label)
        else:
            real_sources.append(label)

    if not real_sources:
        return "> 本路书由占位数据生成，真实路线、天气与补能信息尚未接入。"

    note = f"> {'、'.join(real_sources)}数据来自外部服务"
    if placeholder_sources:
        note += f"；{'、'.join(placeholder_sources)}为占位数据"

    knowledge = ctx.upstream.get(AgentName.KNOWLEDGE.value)
    knowledge_real = knowledge is not None and not knowledge.is_placeholder
    note += "；救援调度尚未接入" if knowledge_real else "；救援调度与知识库尚未接入"
    note += "。"
    return note


def _data_source_lines(ctx: AgentContext) -> list[str]:
    lines: list[str] = []
    for label, agent_name in (
        ("路线", AgentName.ROUTE),
        ("天气", AgentName.WEATHER),
        ("补能", AgentName.ENERGY),
        ("安全资源", AgentName.SAFETY),
        ("知识库", AgentName.KNOWLEDGE),
    ):
        result = ctx.upstream.get(agent_name.value)
        if result is None:
            continue
        if result.data_sources:
            sources = "、".join(
                _SOURCE_LABELS.get(source, source) for source in result.data_sources
            )
            lines.append(f"- {label}：{sources}")
        elif result.is_placeholder:
            lines.append(f"- {label}：占位数据")

    weather = ctx.upstream.get(AgentName.WEATHER.value)
    if weather and "open_meteo" in weather.data_sources:
        lines.append("- 天气数据由 Open-Meteo 提供（CC BY 4.0）")

    return lines


def _llm_configured() -> bool:
    return bool(config.LLM_API_KEY and config.LLM_MODEL)


def _travelogue_with_llm(roadbook: str) -> str | None:
    if not _llm_configured():
        return None

    kwargs: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "api_key": config.LLM_API_KEY,
        "temperature": 0.7,
        "timeout": 30,
        "max_retries": 0,
    }
    if config.LLM_BASE_URL:
        kwargs["base_url"] = config.LLM_BASE_URL

    system = (
        "你是中文旅行写作助手。根据用户提供的自驾行程规划，写一篇 600-900 字的"
        "中文游记草稿。要求：\n"
        "- 只使用给定的路线、天气、地点和安全提示，不要编造具体餐厅、价格、"
        "门票或亲身体验。\n"
        "- 语气自然、有画面感，但明确这是根据规划生成的游记草稿，"
        "不要声称已经真实到访。\n"
        "- 保留数据来源与安全提示的关键信息。\n"
        "- 直接输出 Markdown 正文，不要代码块，不要额外解释。"
    )
    try:
        client = ChatOpenAI(**kwargs)
        response = client.invoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=f"行程规划：\n{roadbook}"),
            ]
        )
        text = _content_to_text(response.content).strip()
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return text.strip() or None
    except Exception as exc:  # noqa: BLE001 - LLM 是可选增强
        logger.warning(
            "游记 LLM 生成失败（%s），改用模板。", type(exc).__name__
        )
        return None


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _travelogue_template(ctx: AgentContext) -> str:
    itinerary = ctx.itinerary
    if itinerary is None:
        return "# 自驾游记（草稿）\n\n当前没有可用行程。"

    request = ctx.request
    lines: list[str] = [
        f"# {request.destination} 自驾游记（草稿）",
        "",
        "> 本游记根据行程规划数据生成，尚未包含真实旅行照片与个人经历。",
        "",
        (
            f"这趟行程从{request.origin}出发，前往{request.destination}，"
            f"计划 {request.days} 天，全程约 {itinerary.total_distance_km:.0f} 公里。"
        ),
        "",
    ]

    if itinerary.cost_estimate is not None:
        lines.extend(
            [
                (
                    "按规划数据粗略估算，能源与过路费约 "
                    f"{itinerary.cost_estimate.total_cny:.0f} 元"
                    "（不含住宿、餐饮和门票）。"
                ),
                "",
            ]
        )

    for day in itinerary.days:
        lines.extend([f"## 第 {day.day_index} 天 · {day.title}", ""])
        for activity in day.activities:
            time_prefix = f"{activity.start_time} " if activity.start_time else ""
            lines.append(f"- {time_prefix}{activity.title}")
        if day.driving_km:
            lines.append(f"- 当日驾车约 {day.driving_km:.0f} 公里")
        if day.notes:
            lines.append(f"- {day.notes}")
        lines.append("")

    weather = ctx.upstream.get(AgentName.WEATHER.value)
    if weather and weather.weather_windows:
        lines.extend(["## 沿途天气", ""])
        for window in weather.weather_windows:
            lines.append(
                f"- 第 {window.day_index} 天：{window.condition}，"
                f"{window.temp_low_c:.0f}–{window.temp_high_c:.0f}℃"
            )
        lines.append("")

    route = ctx.upstream.get(AgentName.ROUTE.value)
    if route and route.route_options:
        lines.extend(["## 路线印象", ""])
        for option in route.route_options[:2]:
            lines.append(
                f"- {option.name}：约 {option.distance_km:.0f} 公里，"
                f"{option.duration_min} 分钟"
            )
        lines.append("")

    safety = ctx.upstream.get(AgentName.SAFETY.value)
    if safety and safety.safety_alerts:
        lines.extend(["## 行前提醒", ""])
        for alert in safety.safety_alerts:
            lines.append(f"- {alert.message}")
        lines.append("")

    lines.extend(["## 数据来源", ""])
    lines.extend(_data_source_lines(ctx) or ["- 暂无"])
    lines.append("")
    return "\n".join(lines).strip()
