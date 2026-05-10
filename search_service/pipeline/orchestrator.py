"""数据预处理编排 —— 加载 → 分块 → embedding → 索引至轻量后端。"""

from __future__ import annotations

import os
import logging

from ..engine.backends.lite.bm25 import LiteBM25Searcher
from ..engine.backends.lite.vector import LiteVectorSearcher
from ..engine.backends.lite.graph import LiteGraphSearcher
from ..engine.backends.lite.embedding import get_embedding_model
from ..config import service_config
from .loader import load_files
from .chunker import chunk_text
from .classifier import classify_document
from .code_parser import parse_java_repo

logger = logging.getLogger(__name__)


def run(
    data_dir: str,
    patterns: list[str] | None = None,
) -> dict:
    """执行完整数据预处理流水线。

    1. 加载文件
    2. 分块
    3. 生成 embedding 并索引到 FAISS
    4. 索引到 SQLite FTS5
    5. 构建 NetworkX 知识图谱

    Returns:
        stats dict
    """
    logger.info("pipeline start: data_dir=%s", data_dir)

    # ── 1. 加载文档 ──
    files = load_files(data_dir, patterns)
    logger.info("loaded %d doc files", len(files))

    # ── 2. 文档分块 ──
    all_chunks: list[dict] = []
    for f in files:
        # 文档分类：目录 → 文件名 → 内容关键词 → 默认 document
        preview = f["content"][:800]
        doc_type = classify_document(f["path"], preview)
        chunk_type = doc_type.value

        # 从路径推断 module
        parts = os.path.dirname(f["path"]).replace("\\", "/").split("/")
        module = parts[0] if parts else ""

        title = os.path.splitext(f["filename"])[0]
        chunks = chunk_text(
            f["content"],
            source_path=f["path"],
            title=title,
            chunk_type=chunk_type,
            module=module,
        )
        all_chunks.extend(chunks)

    logger.info("chunked into %d doc chunks", len(all_chunks))

    java_chunk_count = 0
    # ── 2.5. Java 源码解析 ──
    java_chunks = parse_java_repo(data_dir)
    if java_chunks:
        java_chunk_count = len(java_chunks)
        logger.info("parsed %d java method chunks", java_chunk_count)
        all_chunks.extend(java_chunks)

    # ── 3. BM25 索引 ──
    bm25 = LiteBM25Searcher(service_config.sqlite_db_path)
    bm25_count = bm25.index_documents(all_chunks)
    logger.info("bm25 indexed: %d", bm25_count)

    # ── 4. Embedding + FAISS 索引 ──
    emb = get_embedding_model()
    texts = [c["content"] for c in all_chunks]
    vectors = emb.embed(texts)
    ids = [c["id"] for c in all_chunks]
    metas = [{"type": c["type"], "content": c["content"], "title": c["title"],
              "module": c["module"], "source_path": c["source_path"]}
             for c in all_chunks]

    vector_searcher = LiteVectorSearcher(service_config.faiss_index_dir)
    vec_count = vector_searcher.index_vectors(ids, vectors, metas)
    logger.info("vector indexed: %d", vec_count)

    # ── 5. 图构建 ──
    graph = LiteGraphSearcher(service_config.graph_storage_path)
    for c in all_chunks:
        graph.add_entity(c["id"], c["type"], c["content"], {
            "title": c["title"],
            "module": c["module"],
            "source_path": c["source_path"],
        })
    # 为同 source 的块建立 RELATED_TO 关系
    source_chunks: dict[str, list[str]] = {}
    for c in all_chunks:
        source_chunks.setdefault(c["source_path"], []).append(c["id"])
    for src, chunk_ids in source_chunks.items():
        for i in range(len(chunk_ids) - 1):
            graph.add_relation(chunk_ids[i], chunk_ids[i + 1], "RELATED_TO")

    logger.info("graph nodes: %d", len(all_chunks))

    return {
        "doc_files": len(files),
        "doc_chunks": len(all_chunks) - java_chunk_count,
        "java_chunks": java_chunk_count,
        "total_chunks": len(all_chunks),
        "indexed_bm25": bm25_count,
        "indexed_vector": vec_count,
        "graph_nodes": len(all_chunks),
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    target = sys.argv[1] if len(sys.argv) > 1 else "../doc"
    stats = run(target)
    print(f"\nPipeline complete: {stats}")
