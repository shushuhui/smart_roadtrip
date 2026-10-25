"""RAG 相关目录常量。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"
INDEX_DIR = PROJECT_ROOT / "data" / "vector_store"
