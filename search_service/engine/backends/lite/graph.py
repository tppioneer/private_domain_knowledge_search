"""轻量图遍历 —— 基于 NetworkX 内存图。"""

from __future__ import annotations

import logging
import os

from ....models.schemas import KnowledgeItem, KnowledgeMeta, KnowledgeType, SearchContext

logger = logging.getLogger(__name__)


class LiteGraphSearcher:
    """NetworkX 有向图，存储实体节点和关系边。"""

    def __init__(self, storage_path: str = "./data/graph.json"):
        self.storage_path = storage_path
        self._graph = None
        self._loaded = False

    @property
    def available(self) -> bool:
        return True

    def _load(self):
        if self._loaded:
            return
        import networkx as nx
        self._graph = nx.DiGraph()
        if os.path.exists(self.storage_path):
            import json
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
                for node_id, attrs in data.get("nodes", {}).items():
                    self._graph.add_node(node_id, **attrs)
                for src, tgt, rel_type in data.get("edges", []):
                    self._graph.add_edge(src, tgt, relation=rel_type)
        self._loaded = True

    def _save(self):
        import json
        data = {
            "nodes": {n: dict(self._graph.nodes[n]) for n in self._graph.nodes()},
            "edges": [(u, v, d.get("relation", "")) for u, v, d in self._graph.edges(data=True)],
        }
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_entity(self, doc_id: str, item_type: str, content: str, meta: dict | None = None):
        self._load()
        self._graph.add_node(
            doc_id,
            type=item_type,
            content=content,
            meta=meta or {},
        )
        self._save()

    def add_relation(self, source: str, target: str, relation_type: str = "RELATED_TO"):
        self._load()
        self._graph.add_edge(source, target, relation=relation_type)
        self._save()

    async def search(
        self,
        query: str,
        context: SearchContext | None = None,
        knowledge_types: list[KnowledgeType] | None = None,
        top_k: int = 15,
    ) -> list[KnowledgeItem]:
        self._load()
        if self._graph is None or self._graph.number_of_nodes() == 0:
            return []

        query_lower = query.lower()
        allowed_types = {t.value for t in knowledge_types} if knowledge_types else None
        candidates: list[tuple[str, float]] = []

        for node_id, attrs in self._graph.nodes(data=True):
            content = attrs.get("content", "")
            node_type = attrs.get("type", "")

            if allowed_types and node_type not in allowed_types:
                continue

            if query_lower in content.lower() or query_lower in node_id.lower():
                score = 0.85
                if query_lower in content.lower():
                    score = 0.90 + 0.05 * min(content.lower().count(query_lower), 3)
                candidates.append((node_id, score))

        if not candidates:
            return []

        candidates.sort(key=lambda x: x[1], reverse=True)
        items: list[KnowledgeItem] = []
        seen: set[str] = set()

        for node_id, score in candidates[:top_k]:
            if node_id in seen:
                continue
            seen.add(node_id)
            attrs = self._graph.nodes[node_id]
            related = list(self._graph.neighbors(node_id))
            meta_data = dict(attrs.get("meta", {}))
            if related:
                meta_data["related_entity"] = ", ".join(related[:5])

            try:
                kt = KnowledgeType(attrs.get("type", "api"))
            except ValueError:
                kt = KnowledgeType.API
            items.append(KnowledgeItem(
                id=node_id,
                type=kt,
                content=attrs.get("content", ""),
                score=min(score, 1.0),
                meta=KnowledgeMeta(**meta_data),
            ))

        return items
