"""assemble_prompt 工具入口 —— 串联清洗 → 去重 → 组装流水线。"""

from __future__ import annotations

from ..models.schemas import AssemblePromptInput, AssemblePromptOutput, AssembleStats, PromptSections
from .sanitizer import sanitize_items
from .deduplicator import deduplicate_items
from .assembler import assemble_sections


async def assemble_prompt(input_: AssemblePromptInput) -> AssemblePromptOutput:
    """执行完整流水线，返回组装后的 Prompt 与统计信息。

    流水线: 安全清洗 → 去重 → 分组排序 → Token裁剪 → 4段式组装
    """
    items = input_.search_items or []
    specs = input_.specs or []

    # 1. 安全清洗
    items, sanitization_log = sanitize_items(items)

    # 2. 去重
    deduped = deduplicate_items(items)

    # 3-5. 组装
    sections, stats = assemble_sections(
        items=deduped,
        specs=specs,
        user_query=input_.user_query,
        pinned_knowledge=input_.pinned_knowledge,
        role_hint=input_.role_hint,
        max_tokens=input_.max_tokens,
        context=input_.context,
    )

    # 更新去重后数量
    stats.after_dedup = len(deduped)
    if stats.input_items != len(items):
        stats.input_items = len(items)

    # 拼接完整 Prompt
    parts: list[str] = []
    if sections.system:
        parts.append(sections.system)
    if sections.background:
        parts.append(sections.background)
    if sections.user_request:
        parts.append(sections.user_request)
    if sections.constraints:
        parts.append(sections.constraints)

    assembled = "\n\n".join(parts)

    return AssemblePromptOutput(
        assembled_prompt=assembled,
        sections=sections,
        stats=stats,
        sanitization_log=sanitization_log,
    )
