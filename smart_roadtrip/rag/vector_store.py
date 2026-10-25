"""FAISS 索引的构建、保存与加载。

索引使用归一化向量的内积（等价于余弦相似度），元数据以 JSON 与索引一起保存。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import faiss
import numpy as np

INDEX_FILENAME = "index.faiss"
CHUNKS_FILENAME = "chunks.json"


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    source: str
    title: str = ""


class FaissVectorStore:
    def __init__(self, dimension: int, chunks: list[Chunk] | None = None):
        self.dimension = dimension
        self.index = faiss.IndexFlatIP(dimension)
        self.chunks: list[Chunk] = chunks or []

    @property
    def size(self) -> int:
        return len(self.chunks)

    def add(self, vectors: Any, chunks: list[Chunk]) -> None:
        matrix = np.ascontiguousarray(vectors, dtype=np.float32)
        if matrix.ndim != 2:
            raise ValueError("向量必须是二维数组。")
        if matrix.shape[0] != len(chunks):
            raise ValueError("向量数量与文本片段数量不一致。")
        if matrix.shape[1] != self.dimension:
            raise ValueError("向量维度与索引维度不一致。")

        faiss.normalize_L2(matrix)
        self.index.add(matrix)
        self.chunks.extend(chunks)

    def save(self, index_dir: Path) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(index_dir / INDEX_FILENAME))
        payload = [asdict(chunk) for chunk in self.chunks]
        (index_dir / CHUNKS_FILENAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, index_dir: Path) -> "FaissVectorStore":
        index_path = index_dir / INDEX_FILENAME
        chunks_path = index_dir / CHUNKS_FILENAME
        if not index_path.exists() or not chunks_path.exists():
            raise FileNotFoundError(f"FAISS 索引不存在：{index_dir}")

        index = faiss.read_index(str(index_path))
        payload = json.loads(chunks_path.read_text(encoding="utf-8"))
        chunks = [Chunk(**item) for item in payload]
        store = cls(dimension=index.d, chunks=chunks)
        store.index = index
        return store

    def search(
        self,
        query_vector: Any,
        top_k: int,
    ) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []

        query = np.ascontiguousarray([query_vector], dtype=np.float32)
        if query.shape[1] != self.dimension:
            raise ValueError("查询向量维度与索引维度不一致。")

        faiss.normalize_L2(query)
        limit = max(1, min(int(top_k), len(self.chunks)))
        scores, indices = self.index.search(query, limit)
        hits: list[tuple[Chunk, float]] = []
        for score, index in zip(scores[0], indices[0]):
            if int(index) < 0:
                continue
            hits.append((self.chunks[int(index)], float(score)))
        return hits
