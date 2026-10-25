"""KnowledgeAgent：RAG 自驾知识库检索（景点规则/装备/应急）。

优先检索本地 FAISS 索引；索引缺失、模型不可用或未命中时返回占位片段。
"""

from __future__ import annotations

import logging

from smart_roadtrip import config
from smart_roadtrip.agents.base import AgentContext, BaseAgent, placeholder_result
from smart_roadtrip.schemas.trip import (
    AgentName,
    AgentResult,
    AgentStatus,
    KnowledgeSnippet,
)
from smart_roadtrip.tools import gateway

logger = logging.getLogger(__name__)


class KnowledgeAgent(BaseAgent):
    name = AgentName.KNOWLEDGE

    def _run(self, ctx: AgentContext) -> AgentResult:
        request = ctx.request
        interests = " ".join(request.preferences.interests)
        query = f"{request.destination} 自驾 {interests} 景点规则 装备 应急".strip()

        snippets = []
        if config.RAG_ENABLED:
            try:
                snippets = gateway.rag_retrieve(query, top_k=config.RAG_TOP_K)
            except Exception as exc:  # noqa: BLE001 - 检索失败降级占位
                logger.warning(
                    "知识库检索失败（%s）：%s", type(exc).__name__, exc
                )

        if snippets:
            return AgentResult(
                agent=AgentName.KNOWLEDGE,
                round=ctx.round,
                status=AgentStatus.OK,
                summary=f"从本地知识库检索到 {len(snippets)} 条片段。",
                is_placeholder=False,
                knowledge_snippets=snippets,
                data_sources=["rag_faiss"],
            )

        interests_label = "、".join(request.preferences.interests) or "自驾出行"
        placeholder_snippets = [
            KnowledgeSnippet(
                topic="出发准备",
                title="占位知识：出发前检查清单",
                content=(
                    "检查轮胎、刹车、灯光与油液；携带驾驶证、行驶证与保险凭证；"
                    "高海拔或长途路线建议准备保暖衣物、常用药品与离线地图。"
                ),
                source="data/knowledge（索引未构建或未命中）",
                tags=["装备", "检查", "占位"],
            ),
            KnowledgeSnippet(
                topic="兴趣点",
                title=f"占位知识：{interests_label}相关注意事项",
                content=(
                    "景区开放时间、预约规则与停车信息尚未从知识库检索；"
                    "请先运行 scripts/ingest_knowledge.py 构建索引后重新规划。"
                ),
                source="data/knowledge（索引未构建或未命中）",
                tags=["景点规则", "占位"],
            ),
        ]
        return placeholder_result(
            AgentName.KNOWLEDGE,
            (
                f"本地知识库索引不可用或未命中，返回 {len(placeholder_snippets)} 条占位片段；"
                "请运行 scripts/ingest_knowledge.py 构建索引。"
            ),
            round=ctx.round,
            knowledge_snippets=placeholder_snippets,
        )
