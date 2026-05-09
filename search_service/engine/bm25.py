"""BM25 全文检索 —— 基于 Elasticsearch。"""

from __future__ import annotations

from ..models.schemas import KnowledgeItem, KnowledgeMeta, KnowledgeType, SearchContext
from ..config import service_config


class BM25Searcher:
    """ES BM25 检索器。试点期无可用 ES 时优雅降级返回空结果。"""

    def __init__(self):
        self._es = None
        self._available = None  # 延迟检测

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                from elasticsearch import Elasticsearch
                self._es = Elasticsearch(service_config.es_hosts)
                self._available = self._es.ping()
            except Exception:
                self._available = False
        return self._available

    async def search(
        self,
        query: str,
        context: SearchContext | None = None,
        knowledge_types: list[KnowledgeType] | None = None,
        top_k: int = 15,
    ) -> list[KnowledgeItem]:
        if not self.available:
            return []

        try:
            body = {
                "query": {
                    "bool": {
                        "must": [{"multi_match": {"query": query, "fields": ["content", "title"]}}],
                        "filter": [],
                    }
                },
                "size": top_k,
            }

            if knowledge_types:
                body["query"]["bool"]["filter"].append(
                    {"terms": {"type": [t.value for t in knowledge_types]}}
                )

            if context and context.module:
                body["query"]["bool"]["filter"].append({"term": {"module": context.module}})

            result = self._es.search(index=service_config.es_index, body=body)

            hits = result.get("hits", {}).get("hits", [])
            items: list[KnowledgeItem] = []
            for hit in hits:
                src = hit["_source"]
                items.append(KnowledgeItem(
                    id=hit["_id"],
                    type=KnowledgeType(src.get("type", "api")),
                    content=src.get("content", ""),
                    score=hit["_score"],
                    meta=KnowledgeMeta(**src.get("meta", {})),
                ))
            return items
        except Exception:
            return []
