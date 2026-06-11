"""list_knowledge_repos —— 列出可用的知识库仓库。"""

from __future__ import annotations

from ..models.schemas import ListReposResponse
from ..services.knowledge_base import KnowledgeBase


async def list_knowledge_repos(
    kb: KnowledgeBase,
) -> ListReposResponse:
    return await kb.list_repos()
