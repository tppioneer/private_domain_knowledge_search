"""GET /health —— 健康检查。"""

from __future__ import annotations

from fastapi import APIRouter

from ..models.schemas import HealthStatus

router = APIRouter()


@router.get("/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    # 试点期：逐一检查后端连接状态
    es_ok = _check_es()
    milvus_ok = _check_milvus()
    neo4j_ok = _check_neo4j()

    all_ok = es_ok and milvus_ok and neo4j_ok
    if all_ok:
        status = "ok"
    elif es_ok or milvus_ok:
        status = "degraded"
    else:
        status = "down"

    return HealthStatus(status=status, es=es_ok, milvus=milvus_ok, neo4j=neo4j_ok)


def _check_es() -> bool:
    try:
        from elasticsearch import Elasticsearch
        from ..config import service_config
        client = Elasticsearch(service_config.es_hosts)
        return client.ping()
    except Exception:
        return False


def _check_milvus() -> bool:
    try:
        from pymilvus import connections
        from ..config import service_config
        connections.connect(host=service_config.milvus_host, port=str(service_config.milvus_port), timeout=2)
        connections.disconnect("default")
        return True
    except Exception:
        return False


def _check_neo4j() -> bool:
    try:
        from neo4j import GraphDatabase
        from ..config import service_config
        with GraphDatabase.driver(service_config.neo4j_uri, auth=(service_config.neo4j_user, service_config.neo4j_password)) as driver:
            driver.verify_connectivity()
        return True
    except Exception:
        return False
