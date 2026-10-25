"""构建 RAG 索引的入口脚本。

用法（在仓库根目录）：
    python scripts/ingest_knowledge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 直接执行脚本时，把仓库根目录加入 sys.path，保证能导入 smart_roadtrip。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smart_roadtrip.rag.ingest import ingest  # noqa: E402
from smart_roadtrip.rag.paths import INDEX_DIR, KNOWLEDGE_DIR  # noqa: E402
from smart_roadtrip.rag.retriever import reset_default_retriever  # noqa: E402


def main() -> int:
    try:
        count = ingest(KNOWLEDGE_DIR, INDEX_DIR)
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一提示
        print(f"构建索引失败：{exc}")
        return 1

    reset_default_retriever()
    print(f"已索引 {count} 个片段 -> {INDEX_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
