"""知识库摄取：切分 data/knowledge 文档并向量化写入 FAISS。"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from smart_roadtrip import config
from smart_roadtrip.rag.embeddings import Embedder, get_embedder
from smart_roadtrip.rag.paths import INDEX_DIR, KNOWLEDGE_DIR
from smart_roadtrip.rag.vector_store import Chunk, FaissVectorStore

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}


def load_documents(knowledge_dir: Path = KNOWLEDGE_DIR) -> list[Chunk]:
    """读取知识库目录下的 Markdown/文本文件并切分为片段。"""

    if not knowledge_dir.exists():
        return []

    chunks: list[Chunk] = []
    for path in sorted(knowledge_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            logger.warning("读取知识文件失败（%s）：%s", path.name, exc)
            continue

        source = path.relative_to(knowledge_dir).as_posix()
        title = _title_from(text, path)
        for index, chunk_text in enumerate(
            split_text(
                text,
                max_chars=config.RAG_CHUNK_CHARS,
                overlap=config.RAG_CHUNK_OVERLAP,
            )
        ):
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(source, index, chunk_text),
                    text=chunk_text,
                    source=source,
                    title=title,
                )
            )
    return chunks


def split_text(
    text: str,
    *,
    max_chars: int = 500,
    overlap: int = 80,
) -> list[str]:
    """按段落聚合，超长段落按字符窗口切分。"""

    max_chars = max(100, int(max_chars))
    overlap = max(0, min(int(overlap), max_chars // 2))

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", normalized)
        if paragraph.strip()
    ]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long(paragraph, max_chars, overlap))
            continue

        candidate = f"{current}\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)
    return chunks


def ingest(
    knowledge_dir: Path = KNOWLEDGE_DIR,
    index_dir: Path = INDEX_DIR,
    *,
    embedder: Embedder | None = None,
) -> int:
    """构建并保存 FAISS 索引，返回片段数量。"""

    chunks = load_documents(knowledge_dir)
    if not chunks:
        raise ValueError(
            f"{knowledge_dir} 中没有可索引的 .md/.markdown/.txt 文件。"
        )

    encoder = embedder or get_embedder()
    vectors = encoder.encode(
        [chunk.text for chunk in chunks],
        normalize_embeddings=True,
    )
    if len(vectors) != len(chunks):
        raise ValueError("embedding 数量与文本片段数量不一致。")
    if len(vectors) == 0 or len(vectors[0]) == 0:
        raise ValueError("embedding 为空，无法构建索引。")

    store = FaissVectorStore(dimension=len(vectors[0]))
    store.add(vectors, chunks)
    store.save(index_dir)
    logger.info("已写入 %d 个片段到 %s", len(chunks), index_dir)
    return len(chunks)


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _title_from(text: str, path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return path.stem


def _chunk_id(source: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{source}:{index}:{text}".encode("utf-8")).hexdigest()
    return digest
