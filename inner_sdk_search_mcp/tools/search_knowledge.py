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


_CP_LABELS: dict[str, str] = {
    "builder": "Builder 模式：`{cls}.{method}().build()` 创建实例",
    "static_factory": "静态工厂：`{cls}.{method}(...)` → `{ret}`",
    "singleton": "单例模式：`{cls}.{method}()`",
    "constructor": "构造函数：`new {cls}(...)`",
}


def _build_construction_guide(items: list) -> str:
    """从检索结果中提取入口类的构造方法，生成构造指引文本。"""
    from ..models.schemas import KnowledgeItem, KnowledgeMeta

    entry_cons: dict[str, list[dict]] = {}
    for item in items:
        meta = item.meta if hasattr(item, "meta") else item.get("meta", {})
        if hasattr(meta, "role"):
            role = meta.role
            cp = meta.construction_pattern
            fqn = meta.class_name or ""
            method = meta.method or ""
            rt = meta.return_type or ""
        elif isinstance(meta, dict):
            role = meta.get("role", "")
            cp = meta.get("construction_pattern", "")
            fqn = meta.get("class_name", "")
            method = meta.get("method", "")
            rt = meta.get("return_type", "")
        else:
            continue
        if role == "entry_point" and cp:
            entry_cons.setdefault(fqn, []).append({
                "method": method, "pattern": cp,
                "ret": rt.rsplit(".", 1)[-1] if rt else "",
            })

    if not entry_cons:
        return ""

    lines: list[str] = []
    for fqn, methods in entry_cons.items():
        simple = fqn.rsplit(".", 1)[-1]
        by_pattern: dict[str, list[dict]] = {}
        for m in methods:
            by_pattern.setdefault(m["pattern"], []).append(m)

        for pattern in ("builder", "static_factory", "singleton", "constructor"):
            ms = by_pattern.get(pattern, [])
            if not ms:
                continue
            template = _CP_LABELS.get(pattern, "")
            m = ms[0]
            line = template.format(cls=simple, method=m["method"], ret=m["ret"])
            if len(ms) > 1:
                others = [x["method"] for x in ms[1:]]
                line += f"（另可选 `{'`, `'.join(others)}`）"
            lines.append(line)
            break

    return "；".join(lines)


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

    items, diagnostics, layered_recs = await engine.search(
        query=query,
        context=context,
        knowledge_types=knowledge_types,
        top_k=top_k,
        min_score=min_score,
    )

    guide = _build_construction_guide(items)

    response = SearchKnowledgeResponse(
        items=items, diagnostics=diagnostics,
        layered_recommendations=layered_recs,
        construction_guide=guide,
    )

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
