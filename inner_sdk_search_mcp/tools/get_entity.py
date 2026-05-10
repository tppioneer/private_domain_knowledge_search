"""get_entity_detail —— 精确实体查询。"""

from __future__ import annotations

from ..models.schemas import EntityDetailResponse, EntityType
from ..services.knowledge_base import KnowledgeBase


async def get_entity_detail(
    kb: KnowledgeBase,
    entity_name: str,
    entity_type: EntityType | None = None,
    version_requirement: str | None = None,
) -> EntityDetailResponse:
    result = await kb.get_entity(
        entity_name=entity_name,
        entity_type=entity_type,
        version_requirement=version_requirement,
    )
    if result is None:
        return EntityDetailResponse(
            entity_name=entity_name,
            entity_type=entity_type or EntityType.TERM,
            found=False,
            message=f"未找到实体 '{entity_name}'",
        )
    return result
