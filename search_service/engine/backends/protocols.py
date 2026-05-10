"""后端检索器 Protocol 定义 —— 约束 lite 与 production 实现的公共接口。"""

from __future__ import annotations

from typing import Protocol

from ...models.schemas import KnowledgeItem, KnowledgeType, SearchContext


class SearcherProtocol(Protocol):
    """所有检索器（BM25 / Vector / Graph）的公共接口。

    lite 和 production 实现均隐式满足此协议，factory 返回实例后由 HybridSearcher 消费。
    """

    @property
    def available(self) -> bool:
        ...

    async def search(
        self,
        query: str,
        context: SearchContext | None = None,
        knowledge_types: list[KnowledgeType] | None = None,
        top_k: int = 15,
    ) -> list[KnowledgeItem]:
        ...
