"""数据预处理编排 —— 加载 → 分块 → embedding → 直接写入后端存储。

纯 pipeline 模块，不 import search_service 任何代码。
直接使用 sqlite3 / faiss / networkx / sentence-transformers 底层库写入。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3

from .loader import load_files
from .chunker import chunk_text
from .classifier import classify_document
from .code_parser import _load_rules, parse_java_repo, parse_python_repo, parse_typescript_repo
from .models import ChunkMeta, FileRecord, PipelineChunk

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = os.getenv("SQLITE_DB_PATH", "./data/knowledge.db")
_DEFAULT_FAISS_DIR = os.getenv("FAISS_INDEX_DIR", "./data/faiss")
_DEFAULT_GRAPH_PATH = os.getenv("GRAPH_STORAGE_PATH", "./data/graph.json")
_EMBEDDING_DIM = 512


# ═══════════════════════════════════════════════
# BM25（直接 sqlite3 FTS5）
# ═══════════════════════════════════════════════

def _ensure_fts5(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            doc_id, type, content, title, module, meta_json, tokenize='unicode61'
        )
    """)


def _index_bm25(chunks: list[PipelineChunk]) -> int:
    path = _DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    _ensure_fts5(conn)
    conn.execute("DELETE FROM knowledge_fts")
    count = 0
    for c in chunks:
        conn.execute(
            "INSERT INTO knowledge_fts(doc_id, type, content, title, module, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (c.id, c.type, c.content, c.title, c.module, c.meta.to_json()),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


# ═══════════════════════════════════════════════
# FAISS（直接 faiss + numpy）
# ═══════════════════════════════════════════════

def _index_vectors(ids: list[str], vectors: list[list[float]], metas: list[dict]) -> int:
    if not vectors:
        return 0
    import faiss
    import numpy as np
    d = _DEFAULT_FAISS_DIR
    os.makedirs(d, exist_ok=True)
    vec_array = np.array(vectors, dtype=np.float32)
    index = faiss.IndexFlatIP(vec_array.shape[1])
    index.add(vec_array)
    faiss.write_index(index, os.path.join(d, "vectors.index"))
    with open(os.path.join(d, "id_map.json"), "w", encoding="utf-8") as f:
        json.dump(dict(zip(ids, metas)), f, ensure_ascii=False)
    return len(ids)


# ═══════════════════════════════════════════════
# Graph（直接 networkx + JSON）
# ═══════════════════════════════════════════════

def _build_graph(chunks: list[PipelineChunk]) -> int:
    import networkx as nx
    g = nx.DiGraph()
    for c in chunks:
        node_meta = {
            "title": c.title, "module": c.module, "source_path": c.source_path,
        }
        node_meta.update(_pick_meta_fields(c.meta))
        g.add_node(c.id, type=c.type, content=c.content, meta=node_meta)

    source_chunks: dict[str, list[str]] = {}
    for c in chunks:
        source_chunks.setdefault(c.source_path, []).append(c.id)
    for chunk_ids in source_chunks.values():
        for i in range(len(chunk_ids) - 1):
            g.add_edge(chunk_ids[i], chunk_ids[i + 1], relation="RELATED_TO")

    path = _DEFAULT_GRAPH_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "nodes": {n: dict(g.nodes[n]) for n in g.nodes()},
        "edges": [(u, v, d.get("relation", "")) for u, v, d in g.edges(data=True)],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return g.number_of_nodes()


# ═══════════════════════════════════════════════
# Embedding（直接 sentence-transformers）
# ═══════════════════════════════════════════════

def _get_embedding_model():
    model_path = os.getenv("EMBEDDING_MODEL_PATH", "./models")
    model_name = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh")
    source = model_path if os.path.isdir(model_path) else model_name
    try:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(source)
        logger.info("embedding model loaded: %s (dim=%d)", source, m.get_sentence_embedding_dimension())
        return m
    except Exception:
        logger.warning("embedding unavailable, using zero-vector")
        return None


# ═══════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════

_META_FIELDS = (
    "role", "layer", "requires_context_building", "context_dependencies",
    "standard_context_provider", "class_name", "method", "return_type",
    "return_type_import", "calls", "sdk", "version", "knowledge_source",
    "construction_pattern",
)


def _pick_meta_fields(meta: ChunkMeta) -> dict:
    """提取 ChunkMeta 中非空的结构化字段。"""
    result: dict = {}
    for k in _META_FIELDS:
        v = getattr(meta, k, None)
        if v or (isinstance(v, bool) and v is True):
            result[k] = v
    return result


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def run(data_dir: str, patterns: list[str] | None = None) -> dict:
    logger.info("pipeline start: data_dir=%s", data_dir)

    rules = _load_rules(data_dir)
    file_filters = rules.get("file_filters", {})

    # 1. 文档加载
    files: list[FileRecord] = load_files(
        data_dir, patterns,
        exclude_dirs=set(file_filters.get("exclude_dirs", [])),
        include_extensions=file_filters.get("include_extensions"),
        exclude_extensions=file_filters.get("exclude_extensions"),
    )
    logger.info("loaded %d doc files", len(files))

    # 2. 文档分块
    all_chunks: list[PipelineChunk] = []
    for f in files:
        doc_type = classify_document(f.path, f.content[:800])
        parts = os.path.dirname(f.path).replace("\\", "/").split("/")
        module = parts[0] if parts else ""
        title = os.path.splitext(f.filename)[0]
        chunks = chunk_text(f.content, source_path=f.path, title=title,
                            chunk_type=doc_type, module=module)
        all_chunks.extend(chunks)
    logger.info("chunked into %d doc chunks", len(all_chunks))

    # 2.5. Java 解析
    java_chunks: list[PipelineChunk] = parse_java_repo(data_dir)
    if java_chunks:
        logger.info("parsed %d java method chunks", len(java_chunks))
        all_chunks.extend(java_chunks)

    python_chunks = parse_python_repo(data_dir)
    if python_chunks:
        logger.info("parsed %d python method chunks", len(python_chunks))
        all_chunks.extend(python_chunks)

    ts_chunks = parse_typescript_repo(data_dir)
    if ts_chunks:
        logger.info("parsed %d typescript method chunks", len(ts_chunks))
        all_chunks.extend(ts_chunks)

    # 3. BM25
    bm25_count = _index_bm25(all_chunks)
    logger.info("bm25 indexed: %d", bm25_count)

    # 4. FAISS
    model = _get_embedding_model()
    texts = [c.content for c in all_chunks]
    if model:
        embeddings = model.encode(texts, normalize_embeddings=True)
        vectors = [e.tolist() for e in embeddings]
    else:
        vectors = [[0.0] * _EMBEDDING_DIM for _ in texts]
    ids = [c.id for c in all_chunks]
    metas = [{**{"type": c.type, "content": c.content, "title": c.title,
                  "module": c.module, "source_path": c.source_path},
              **_pick_meta_fields(c.meta)} for c in all_chunks]
    vec_count = _index_vectors(ids, vectors, metas)
    logger.info("vector indexed: %d", vec_count)

    # 5. Graph
    graph_nodes = _build_graph(all_chunks)
    logger.info("graph nodes: %d", graph_nodes)

    return {
        "doc_files": len(files),
        "doc_chunks": len(all_chunks) - len(java_chunks) - len(python_chunks) - len(ts_chunks),
        "java_chunks": len(java_chunks),
        "python_chunks": len(python_chunks),
        "typescript_chunks": len(ts_chunks),
        "total_chunks": len(all_chunks),
        "indexed_bm25": bm25_count,
        "indexed_vector": vec_count,
        "graph_nodes": graph_nodes,
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    target = sys.argv[1] if len(sys.argv) > 1 else "../doc"
    stats = run(target)
    print(f"\nPipeline complete: {stats}")
