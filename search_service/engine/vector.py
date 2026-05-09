"""向量语义检索 —— 基于 Milvus / Qdrant。"""

from __future__ import annotations

from ..models.schemas import KnowledgeItem, KnowledgeMeta, KnowledgeType, SearchContext
from ..config import service_config


class VectorSearcher:
    """向量检索器。试点期无可用 Milvus 时优雅降级返回空结果。"""

    def __init__(self):
        self._available = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                from pymilvus import Collection, connections
                connections.connect(
                    host=service_config.milvus_host,
                    port=str(service_config.milvus_port),
                    timeout=3,
                )
                self._collection = Collection(service_config.milvus_collection)
                self._collection.load()
                self._available = True
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
            # 试点期：用简单文本向量占位，真实场景接入 embedding 模型（如 bge-large-zh）
            embedding = self._embed_query(query)

            search_params = {"metric_type": "IP", "params": {"nprobe": 10}}
            results = self._collection.search(
                data=[embedding],
                anns_field="vector",
                param=search_params,
                limit=top_k,
            )

            items: list[KnowledgeItem] = []
            for hits in results:
                for hit in hits:
                    entity = hit.entity
                    items.append(KnowledgeItem(
                        id=str(entity.get("doc_id", hit.id)),
                        type=KnowledgeType(entity.get("type", "api")),
                        content=entity.get("content", ""),
                        score=float(hit.score),
                        meta=KnowledgeMeta(**entity.get("meta", {})),
                    ))
            return items
        except Exception:
            return []

    @staticmethod
    def _embed_query(query: str) -> list[float]:
        """占位 embedding，试点期替换为 bge-large-zh 调用。"""
        return [0.0] * 768
