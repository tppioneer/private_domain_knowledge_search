"""Search Service 配置。"""

from __future__ import annotations

import os


class ServiceConfig:
    # 服务端口
    host: str = os.getenv("SEARCH_HOST", "0.0.0.0")
    port: int = int(os.getenv("SEARCH_PORT", "8080"))

    # 后端模式: "lightweight" | "production"
    backend_mode: str = os.getenv("BACKEND_MODE", "lightweight")

    # 轻量模式路径
    sqlite_db_path: str = os.getenv("SQLITE_DB_PATH", "./data/knowledge.db")
    faiss_index_dir: str = os.getenv("FAISS_INDEX_DIR", "./data/faiss")
    graph_storage_path: str = os.getenv("GRAPH_STORAGE_PATH", "./data/graph.json")

    # Elastisearch
    es_hosts: list[str] = os.getenv("ES_HOSTS", "").split(",") if os.getenv("ES_HOSTS") else ["http://localhost:9200"]
    es_index: str = os.getenv("ES_INDEX", "private_knowledge")

    # Milvus
    milvus_host: str = os.getenv("MILVUS_HOST", "localhost")
    milvus_port: int = int(os.getenv("MILVUS_PORT", "19530"))
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "knowledge_vectors")

    # Neo4j
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")

    # 检索参数
    candidate_multiplier: int = int(os.getenv("CANDIDATE_MULTIPLIER", "3"))  # 每路检索取 top_k * N 候选
    search_timeout_ms: int = int(os.getenv("SEARCH_TIMEOUT_MS", "300"))


service_config = ServiceConfig()
