"""知识库检索接口，供 KnowledgeAgent 调用。

索引与 embedding 模型均延迟加载；索引缺失、模型下载失败或维度不匹配时
返回空列表，由 Agent 降级到占位知识。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from smart_roadtrip import config
from smart_roadtrip.rag.embeddings import Embedder
from smart_roadtrip.rag.paths import INDEX_DIR
from smart_roadtrip.rag.vector_store import FaissVectorStore
from smart_roadtrip.schemas.trip import KnowledgeSnippet

logger = logging.getLogger(__name__)


class RagRetriever:
    def __init__(
        self,
        index_dir: Path = INDEX_DIR,
        *,
        embedder: Embedder | None = None,
    ):
        self.index_dir = index_dir
        self._embedder = embedder
        self._store: FaissVectorStore | None = None

    def is_available(self) -> bool:
        return (
            (self.index_dir / "index.faiss").exists()
            and (self.index_dir / "chunks.json").exists()
        )

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
    ) -> list[KnowledgeSnippet]:
        if not query.strip() or not self.is_available():
            return []

        try:
            store = self._get_store()
            embedder = self._get_embedder()
            vectors = embedder.encode([query], normalize_embeddings=True)
            if len(vectors) == 0:
                return []
            hits = store.search(
                vectors[0],
                top_k if top_k is not None else config.RAG_TOP_K,
            )
        except Exception as exc:  # noqa: BLE001 - 检索失败由 Agent 降级
            logger.warning(
                "知识库检索失败（%s）：%s", type(exc).__name__, exc
            )
            return []

        return [
            KnowledgeSnippet(
                topic="自驾知识",
                title=chunk.title or chunk.source,
                content=chunk.text,
                source=f"{chunk.source}#{chunk.chunk_id[:8]}",
                tags=[f"score={score:.3f}"],
            )
            for chunk, score in hits
        ]

    def _get_store(self) -> FaissVectorStore:
        if self._store is None:
            self._store = FaissVectorStore.load(self.index_dir)
        return self._store

    def _get_embedder(self) -> Embedder:
        if self._embedder is None:
            from smart_roadtrip.rag.embeddings import get_embedder

            self._embedder = get_embedder()
        return self._embedder


_default_retriever: RagRetriever | None = None
_lock = threading.Lock()


def get_default_retriever() -> RagRetriever:
    global _default_retriever
    with _lock:
        if _default_retriever is None:
            _default_retriever = RagRetriever()
        return _default_retriever


def reset_default_retriever() -> None:
    global _default_retriever
    with _lock:
        _default_retriever = None


def is_available() -> bool:
    return get_default_retriever().is_available()


def retrieve(
    query: str,
    *,
    top_k: int | None = None,
) -> list[KnowledgeSnippet]:
    return get_default_retriever().retrieve(query, top_k=top_k)
