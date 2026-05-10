"""混合检索引擎接口 —— BM25 + 向量 + 图遍历。

当前提供 Mock 实现，后续替换为对接 Milvus/ES/Neo4j 的真实实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.schemas import Context, Diagnostics, KnowledgeItem, KnowledgeType


class SearchEngine(ABC):
    """混合检索引擎抽象接口。"""

    @abstractmethod
    async def search(
        self,
        query: str,
        context: Context | None = None,
        knowledge_types: list[KnowledgeType] | None = None,
        top_k: int = 5,
        min_score: float = 0.7,
    ) -> tuple[list[KnowledgeItem], Diagnostics]:
        ...


class MockSearchEngine(SearchEngine):
    """Mock 实现，用于开发和测试。返回空结果 + 诊断信息。"""

    async def search(
        self,
        query: str,
        context: Context | None = None,
        knowledge_types: list[KnowledgeType] | None = None,
        top_k: int = 5,
        min_score: float = 0.7,
    ) -> tuple[list[KnowledgeItem], Diagnostics]:
        return [], Diagnostics(total_scanned=0, time_ms=0)
