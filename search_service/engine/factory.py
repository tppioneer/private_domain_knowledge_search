"""检索引擎工厂 —— 根据 BACKEND_MODE 创建对应实现。

BACKEND_MODE=lightweight → LiteBM25Searcher / LiteVectorSearcher / LiteGraphSearcher
BACKEND_MODE=production  → BM25Searcher / VectorSearcher / GraphSearcher

多仓支持：传入 repo 参数自动解析为 {repo_data_dir}/{repo}/ 路径。
"""

from __future__ import annotations

import os

from ..config import service_config
from .backends.protocols import SearcherProtocol


def _repo_path(repo: str, default_path: str, sub_path: str) -> str:
    """解析仓库级数据路径。有 repo 用 {repo_dir}/{repo}/{sub}，无则用默认。"""
    if repo and service_config.repo_data_dir:
        return os.path.join(service_config.repo_data_dir, repo, sub_path)
    return default_path


def create_bm25_searcher(repo: str = "") -> SearcherProtocol:
    if service_config.backend_mode == "production":
        from .backends.production.bm25 import BM25Searcher
        return BM25Searcher()
    from .backends.lite.bm25 import LiteBM25Searcher
    return LiteBM25Searcher(_repo_path(repo, service_config.sqlite_db_path, "knowledge.db"))


def create_vector_searcher(repo: str = "") -> SearcherProtocol:
    if service_config.backend_mode == "production":
        from .backends.production.vector import VectorSearcher
        return VectorSearcher()
    from .backends.lite.vector import LiteVectorSearcher
    return LiteVectorSearcher(_repo_path(repo, service_config.faiss_index_dir, "faiss"))


def create_graph_searcher(repo: str = "") -> SearcherProtocol:
    if service_config.backend_mode == "production":
        from .backends.production.graph import GraphSearcher
        return GraphSearcher()
    from .backends.lite.graph import LiteGraphSearcher
    return LiteGraphSearcher(_repo_path(repo, service_config.graph_storage_path, "graph.json"))


def create_entity_searcher(repo: str = ""):
    """创建实体查询器 —— lite 模式用 SQLite，production 待实现。"""
    if service_config.backend_mode == "production":
        from .backends.lite.entity import LiteEntitySearcher
        return LiteEntitySearcher(service_config.sqlite_db_path)
    from .backends.lite.entity import LiteEntitySearcher
    return LiteEntitySearcher(_repo_path(repo, service_config.sqlite_db_path, "knowledge.db"))
