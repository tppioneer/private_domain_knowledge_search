"""数据预处理编排 —— 加载 → 分块 → embedding → 直接写入后端存储。

纯 pipeline 模块，不 import search_service 任何代码。
直接使用 sqlite3 / faiss / networkx / sentence-transformers 底层库写入。

分仓模式：python -m pipeline.orchestrator --repo my-sdk /path/to/repo
单仓模式（兼容旧行为）：python -m pipeline.orchestrator /path/to/repo
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .loader import load_files
from .chunker import chunk_text
from .classifier import classify_document
from .code_parser import _load_rules, parse_java_repo, parse_python_repo, parse_typescript_repo
from .models import ChunkMeta, FileRecord, PipelineChunk

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = os.getenv("PIPELINE_DATA_DIR", "./data")
_EMBEDDING_DIM = 512

# 全局模块变量，在 run() 入口设置
_db_path = ""
_faiss_dir = ""
_graph_path = ""


# ═══════════════════════════════════════════════
# 路径解析
# ═══════════════════════════════════════════════

def _resolve_paths(data_dir: str, repo_name: str = "") -> tuple[str, str, str]:
    """解析数据存储路径。有 repo_name 时用 data/repos/{repo}/，否则用 data/。"""
    global _db_path, _faiss_dir, _graph_path
    if repo_name:
        base = os.path.join(data_dir, "repos", repo_name)
    else:
        base = data_dir
    os.makedirs(base, exist_ok=True)
    _db_path = os.path.join(base, "knowledge.db")
    _faiss_dir = os.path.join(base, "faiss")
    _graph_path = os.path.join(base, "graph.json")
    return _db_path, _faiss_dir, _graph_path


# ═══════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════

def _save_registry_entry(data_dir: str, repo_name: str, source_dir: str, stats: dict) -> None:
    """写入仓库注册表。"""
    reg_path = os.path.join(data_dir, "repos", "registry.json")
    registry: dict = {}
    if os.path.exists(reg_path):
        try:
            with open(reg_path, encoding="utf-8") as f:
                registry = json.load(f)
        except Exception:
            pass
    repos = registry.setdefault("repos", {})
    repos[repo_name] = {
        "name": repo_name,
        "path": os.path.abspath(source_dir),
        "last_indexed": datetime.now(timezone.utc).isoformat(),
        "chunk_count": stats.get("total_chunks", 0),
        "java_chunks": stats.get("java_chunks", 0),
        "python_chunks": stats.get("python_chunks", 0),
        "typescript_chunks": stats.get("typescript_chunks", 0),
    }
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    logger.info("registry updated: %s", repo_name)


# ═══════════════════════════════════════════════
# BM25
# ═══════════════════════════════════════════════

def _ensure_fts5(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            doc_id, type, content, title, module, meta_json, tokenize='unicode61'
        )
    """)


def _index_bm25(chunks: list[PipelineChunk], clear: bool = True) -> int:
    path = _db_path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    _ensure_fts5(conn)
    if clear:
        conn.execute("DELETE FROM knowledge_fts")
        existing_ids: set = set()
    else:
        rows = conn.execute("SELECT doc_id FROM knowledge_fts").fetchall()
        existing_ids = {r[0] for r in rows}
    count = 0
    for c in chunks:
        if c.id in existing_ids:
            continue
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
# FAISS
# ═══════════════════════════════════════════════

def _index_vectors(ids: list[str], vectors, metas: list[dict]) -> int:
    """vectors 接受 numpy array 或 list[list[float]]。"""
    import faiss
    import numpy as np
    if isinstance(vectors, np.ndarray):
        if vectors.shape[0] == 0:
            return 0
        vec_array = vectors.astype(np.float32)
    else:
        if not vectors:
            return 0
        vec_array = np.array(vectors, dtype=np.float32)
    d = _faiss_dir
    os.makedirs(d, exist_ok=True)
    index = faiss.IndexFlatIP(vec_array.shape[1])
    index.add(vec_array)
    faiss.write_index(index, os.path.join(d, "vectors.index"))
    with open(os.path.join(d, "id_map.json"), "w", encoding="utf-8") as f:
        json.dump(dict(zip(ids, metas)), f, ensure_ascii=False)
    return len(ids)


# ═══════════════════════════════════════════════
# Graph
# ═══════════════════════════════════════════════

def _build_graph(chunks: list[PipelineChunk]) -> int:
    import networkx as nx
    g = nx.DiGraph()
    for c in chunks:
        node_meta = {"title": c.title, "module": c.module, "source_path": c.source_path}
        node_meta.update(_pick_meta_fields(c.meta))
        g.add_node(c.id, type=c.type, content=c.content, meta=node_meta)

    source_chunks: dict[str, list[str]] = {}
    for c in chunks:
        source_chunks.setdefault(c.source_path, []).append(c.id)
    for chunk_ids in source_chunks.values():
        for i in range(len(chunk_ids) - 1):
            g.add_edge(chunk_ids[i], chunk_ids[i + 1], relation="RELATED_TO")

    os.makedirs(os.path.dirname(_graph_path), exist_ok=True)
    data = {
        "nodes": {n: dict(g.nodes[n]) for n in g.nodes()},
        "edges": [(u, v, d.get("relation", "")) for u, v, d in g.edges(data=True)],
    }
    with open(_graph_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return g.number_of_nodes()


# ═══════════════════════════════════════════════
# Embedding
# ═══════════════════════════════════════════════

def _get_embedding_model():
    model_path = os.getenv("EMBEDDING_MODEL_PATH", "./models/bge-small-zh")
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
    result: dict = {}
    for k in _META_FIELDS:
        v = getattr(meta, k, None)
        if v or (isinstance(v, bool) and v is True):
            result[k] = v
    return result


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def run(
    source_dir: str,
    patterns: list[str] | None = None,
    repo_name: str = "",
    data_dir: str = "",
) -> dict:
    """执行完整数据预处理流水线。

    Args:
        source_dir: 源码目录
        patterns: 文档 glob 模式
        repo_name: 仓库名（有则在 {data_dir}/repos/{repo}/ 下写入，无则直接写 {data_dir}）
        data_dir: 数据根目录（默认 ./data，可用 PIPELINE_DATA_DIR 环境变量覆盖）
    """
    global _db_path, _faiss_dir, _graph_path
    out_dir = data_dir or _DEFAULT_DATA_DIR
    if not repo_name:
        repo_name = os.path.basename(os.path.abspath(source_dir))
    _resolve_paths(out_dir, repo_name)
    clear = os.getenv("PIPELINE_CLEAR", "true").lower() in ("true", "1", "yes")

    logger.info("pipeline start: source=%s repo=%s db=%s", source_dir, repo_name, _db_path)

    rules = _load_rules(source_dir)
    file_filters = rules.get("file_filters", {})

    # 1. 文档加载
    files: list[FileRecord] = load_files(
        source_dir, patterns,
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

    # 2.5. 多语言解析
    java_chunks = parse_java_repo(source_dir)
    if java_chunks:
        logger.info("parsed %d java method chunks", len(java_chunks))
        all_chunks.extend(java_chunks)

    python_chunks = parse_python_repo(source_dir)
    if python_chunks:
        logger.info("parsed %d python method chunks", len(python_chunks))
        all_chunks.extend(python_chunks)

    ts_chunks = parse_typescript_repo(source_dir)
    if ts_chunks:
        logger.info("parsed %d typescript method chunks", len(ts_chunks))
        all_chunks.extend(ts_chunks)

    # 3. BM25
    bm25_count = _index_bm25(all_chunks, clear=clear)
    logger.info("bm25 indexed: %d (new=%d, clear=%s)", bm25_count, bm25_count, clear)

    # 4. FAISS
    model = _get_embedding_model()
    if model:
        batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "1000"))
        texts = [c.content for c in all_chunks]
        embeddings = model.encode(
            texts, normalize_embeddings=True,
            batch_size=batch_size, show_progress_bar=True,
        )
    else:
        import numpy as np
        embeddings = np.zeros((len(all_chunks), _EMBEDDING_DIM), dtype=np.float32)
    ids = [c.id for c in all_chunks]
    metas = [{**{"type": c.type, "content": c.content, "title": c.title,
                  "module": c.module, "source_path": c.source_path},
              **_pick_meta_fields(c.meta)} for c in all_chunks]
    vec_count = _index_vectors(ids, embeddings, metas)
    logger.info("vector indexed: %d", vec_count)

    # 5. Graph
    graph_nodes = _build_graph(all_chunks)
    logger.info("graph nodes: %d", graph_nodes)

    stats = {
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

    if repo_name:
        _save_registry_entry(out_dir, repo_name, source_dir, stats)

    return stats


# ═══════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Pipeline 数据导入")
    ap.add_argument("source", nargs="?", default="", help="源码目录路径")
    ap.add_argument("--repo", default="", help="仓库名（默认取目录名）")
    ap.add_argument("--data-dir", default=_DEFAULT_DATA_DIR,
                    help="数据存储根目录（默认 %s）" % _DEFAULT_DATA_DIR)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    target = args.source or "../doc"
    stats = run(target, repo_name=args.repo, data_dir=args.data_dir)
    print(f"\nPipeline complete: {stats}")
