"""recommend_context —— 项目上下文预加载。"""

from __future__ import annotations

from ..models.schemas import ProjectMeta, RecommendContextResponse
from ..services.knowledge_base import KnowledgeBase


async def recommend_context(
    kb: KnowledgeBase,
    project_meta: ProjectMeta,
) -> RecommendContextResponse:
    pinned = await kb.recommend(project_meta)
    return RecommendContextResponse(pinned_knowledge=pinned)
