"""Embedding 模型封装。

延迟导入 sentence-transformers，避免仅导入包时下载模型。
测试通过传入自定义 embedder 绕过模型下载。
"""

from __future__ import annotations

from typing import Any, Protocol

from smart_roadtrip import config


class Embedder(Protocol):
    def encode(
        self,
        texts: list[str],
        normalize_embeddings: bool = True,
    ) -> Any: ...


_model: Embedder | None = None


def get_embedder() -> Embedder:
    global _model
    if _model is None:
        # 延迟导入：首次真正需要向量化时才加载。
        from sentence_transformers import SentenceTransformer

        try:
            # 优先使用本地缓存，避免每次启动都访问 Hugging Face Hub。
            _model = SentenceTransformer(
                config.RAG_EMBEDDING_MODEL, local_files_only=True
            )
        except Exception:
            # 本地没有缓存时才允许联网下载。
            _model = SentenceTransformer(config.RAG_EMBEDDING_MODEL)
    return _model


def reset_embedder() -> None:
    global _model
    _model = None
