"""get_applicable_spec —— 获取适用规范契约。"""

from __future__ import annotations

from ..models.schemas import SpecResponse
from ..services.knowledge_base import KnowledgeBase


async def get_applicable_spec(
    kb: KnowledgeBase,
    module: str | None = None,
    file_path: str | None = None,
    dependency_constraints: dict[str, str] | None = None,
) -> SpecResponse:
    specs, conflict_warnings = await kb.get_specs(
        module=module,
        file_path=file_path,
        dependency_constraints=dependency_constraints,
    )
    return SpecResponse(specs=specs, conflict_warnings=conflict_warnings)
