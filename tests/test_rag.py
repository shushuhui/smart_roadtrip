"""RAG 摄取/检索测试：使用确定性假 embedder，不下载模型。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from smart_roadtrip.agents import knowledge_agent as knowledge_agent_module
from smart_roadtrip.agents.base import AgentContext
from smart_roadtrip.agents.knowledge_agent import KnowledgeAgent
from smart_roadtrip.rag.ingest import ingest, split_text
from smart_roadtrip.rag.retriever import RagRetriever
from smart_roadtrip.schemas.trip import (
    KnowledgeSnippet,
    Traveler,
    TravelPreferences,
    TripRequest,
)


class FakeEmbedder:
    """字符哈希向量，足以验证索引与排序逻辑。"""

    def encode(self, texts: list[str], normalize_embeddings: bool = True):
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * 64
            for char in text:
                vector[ord(char) % 64] += 1.0
            vectors.append(vector)
        return vectors


def _request() -> TripRequest:
    return TripRequest(
        origin="成都",
        destination="稻城亚丁",
        days=3,
        start_date=date(2026, 10, 26),
        travelers=[Traveler(name="我", is_driver=True)],
        preferences=TravelPreferences(interests=["雪山", "摄影"]),
    )


def _write_corpus(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "highland.md").write_text(
        "# 高原自驾\n\n高原反应要缓慢上升海拔，注意休息和补水。",
        encoding="utf-8",
    )
    (root / "equipment.md").write_text(
        "# 装备清单\n\n随车携带拖车绳、充气泵、急救包和保暖衣物。",
        encoding="utf-8",
    )


def test_ingest_and_retrieve(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "knowledge"
    index_dir = tmp_path / "index"
    _write_corpus(knowledge_dir)

    count = ingest(knowledge_dir, index_dir, embedder=FakeEmbedder())
    assert count == 2
    assert (index_dir / "index.faiss").exists()
    assert (index_dir / "chunks.json").exists()

    retriever = RagRetriever(index_dir, embedder=FakeEmbedder())
    snippets = retriever.retrieve("高原反应", top_k=1)

    assert len(snippets) == 1
    assert "高原" in snippets[0].content
    assert snippets[0].source.startswith("highland.md#")


def test_split_text_respects_max_chars() -> None:
    chunks = split_text("A" * 1200, max_chars=500, overlap=80)

    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "".join(chunks).startswith("A")


def test_missing_index_returns_empty(tmp_path: Path) -> None:
    retriever = RagRetriever(tmp_path / "missing", embedder=FakeEmbedder())
    assert retriever.retrieve("高原") == []


def test_knowledge_agent_uses_retriever(monkeypatch) -> None:
    monkeypatch.setattr(knowledge_agent_module.config, "RAG_ENABLED", True)
    monkeypatch.setattr(
        knowledge_agent_module.gateway,
        "rag_retrieve",
        lambda query, top_k: [
            KnowledgeSnippet(
                topic="自驾知识",
                title="高原自驾",
                content="高原反应要缓慢上升海拔。",
                source="highland.md#abc",
            )
        ],
    )

    result = KnowledgeAgent().run(AgentContext(request=_request()))

    assert result.is_placeholder is False
    assert result.data_sources == ["rag_faiss"]
    assert result.knowledge_snippets[0].content == "高原反应要缓慢上升海拔。"


def test_knowledge_agent_falls_back_when_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_agent_module.gateway,
        "rag_retrieve",
        lambda query, top_k: [],
    )

    result = KnowledgeAgent().run(AgentContext(request=_request()))

    assert result.is_placeholder is True
    assert result.knowledge_snippets
    assert all("占位" in snippet.title for snippet in result.knowledge_snippets)
