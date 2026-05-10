"""检索引擎工厂 —— 根据 BACKEND_MODE 创建对应实现。

BACKEND_MODE=lightweight → LiteBM25Searcher / LiteVectorSearcher / LiteGraphSearcher
BACKEND_MODE=production  → BM25Searcher / VectorSearcher / GraphSearcher
"""

from __future__ import annotations

from ..config import service_config
from .backends.protocols import SearcherProtocol


def create_bm25_searcher() -> SearcherProtocol:
    if service_config.backend_mode == "production":
        from .backends.production.bm25 import BM25Searcher
        return BM25Searcher()
    from .backends.lite.bm25 import LiteBM25Searcher
    return LiteBM25Searcher(service_config.sqlite_db_path)


def create_vector_searcher() -> SearcherProtocol:
    if service_config.backend_mode == "production":
        from .backends.production.vector import VectorSearcher
        return VectorSearcher()
    from .backends.lite.vector import LiteVectorSearcher
    return LiteVectorSearcher(service_config.faiss_index_dir)


def create_graph_searcher() -> SearcherProtocol:
    if service_config.backend_mode == "production":
        from .backends.production.graph import GraphSearcher
        return GraphSearcher()
    from .backends.lite.graph import LiteGraphSearcher
    return LiteGraphSearcher(service_config.graph_storage_path)
