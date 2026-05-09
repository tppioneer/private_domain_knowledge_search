"""图遍历检索 —— 基于 Neo4j 代码知识图谱。"""

from __future__ import annotations

from ..models.schemas import KnowledgeItem, KnowledgeMeta, KnowledgeType, SearchContext
from ..config import service_config


class GraphSearcher:
    """图检索器。试点期无可用 Neo4j 时优雅降级返回空结果。"""

    def __init__(self):
        self._driver = None
        self._available = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                from neo4j import GraphDatabase
                self._driver = GraphDatabase.driver(
                    service_config.neo4j_uri,
                    auth=(service_config.neo4j_user, service_config.neo4j_password),
                )
                self._driver.verify_connectivity()
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
            # 根据查询中的实体名查找关联的 API、文档、规范
            cypher = """
            MATCH (n)
            WHERE n.name CONTAINS $keyword
               OR n.content CONTAINS $keyword
            OPTIONAL MATCH (n)-[:RELATED_TO|CALLS|IMPLEMENTS]->(related)
            RETURN n, collect(DISTINCT related.name) AS related_entities
            LIMIT $limit
            """
            with self._driver.session() as session:
                result = session.run(cypher, keyword=query[:80], limit=top_k)
                items: list[KnowledgeItem] = []
                for record in result:
                    node = record["n"]
                    related = record["related_entities"]
                    items.append(KnowledgeItem(
                        id=node.get("doc_id", ""),
                        type=KnowledgeType(node.get("type", "api")),
                        content=node.get("content", ""),
                        score=0.85,
                        meta=KnowledgeMeta(
                            related_entity=", ".join(related) if related else None,
                            **node.get("meta", {}),
                        ),
                    ))
            return items
        except Exception:
            return []
