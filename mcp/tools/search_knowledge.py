"""search_private_knowledge —— 通用混合检索。"""

from __future__ import annotations

import logging

from ..models.schemas import (
    AssemblePromptInput,
    AutoAssembleConfig,
    Context,
    KnowledgeType,
    SearchKnowledgeResponse,
)
from ..services.knowledge_base import KnowledgeBase
from ..services.search_engine import SearchEngine

logger = logging.getLogger(__name__)


async def search_private_knowledge(
    engine: SearchEngine,
    query: str,
    context: Context | None = None,
    knowledge_types: list[KnowledgeType] | None = None,
    top_k: int = 5,
    min_score: float = 0.7,
    auto_assemble: AutoAssembleConfig | None = None,
    kb: KnowledgeBase | None = None,
) -> SearchKnowledgeResponse:
    top_k = min(top_k, 10)

    items, diagnostics = await engine.search(
        query=query,
        context=context,
        knowledge_types=knowledge_types,
        top_k=top_k,
        min_score=min_score,
    )

    response = SearchKnowledgeResponse(items=items, diagnostics=diagnostics)

    if auto_assemble and auto_assemble.enabled and items:
        from ..prompt.assemble_prompt import assemble_prompt as run_assemble_prompt

        specs: list[dict] = []
        if auto_assemble.include_specs:
            if kb is None:
                logger.info(
                    "search_private_knowledge.skip_specs",
                    reason="knowledge_base_unavailable",
                    query=query[:50],
                )
            elif context is None:
                logger.info(
                    "search_private_knowledge.skip_specs",
                    reason="context_not_provided",
                    query=query[:50],
                    message="未提供上下文，无法推断模块，Spec规范已跳过"
                )
            elif context.module is None:
                logger.info(
                    "search_private_knowledge.skip_specs",
                    reason="module_not_in_context",
                    query=query[:50],
                    file_path=context.file_path,
                    message="上下文中无module字段，无法匹配Spec规范，已跳过"
                )
            else:
                spec_result, _ = await kb.get_specs(module=context.module)
                specs = [s.model_dump() for s in spec_result]
                logger.debug(
                    "search_private_knowledge.specs_loaded",
                    module=context.module,
                    spec_count=len(specs),
                )

        ctx_dict = context.model_dump() if context else None

        assemble_input = AssemblePromptInput(
            user_query=query,
            context=ctx_dict,
            search_items=[item.model_dump() for item in items],
            specs=specs,
            max_tokens=auto_assemble.max_tokens,
            role_hint=auto_assemble.role_hint,
        )
        response.assembled_prompt = await run_assemble_prompt(assemble_input)

    return response
