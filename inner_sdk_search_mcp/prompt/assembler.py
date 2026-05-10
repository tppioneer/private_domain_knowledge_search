"""Prompt组装核心 —— 分组排序 + Token裁剪 + 4段式输出。"""

from __future__ import annotations

import re

from ..models.schemas import AssembleStats, PromptSections

# 知识类型在Prompt中的显示顺序（靠前 = 更靠前出现在背景知识中）
_TYPE_ORDER: dict[str, int] = {
    "term": 0,
    "api": 1,
    "best_practice": 2,
    "defect_history": 3,
    "security_rule": 4,
    "spec": 5,
    "test_template": 6,
}

# 中文与英文的 token 估算系数
_TOKEN_PER_CHAR_EN = 2.5
_TOKEN_PER_CHAR_CN = 1.5

_SYSTEM_PREAMBLE: str = (
    "你是一个{role_hint}，请严格遵守以下规范约束。\n\n"
    "## 强制规范（必须遵守）\n"
)

_BACKGROUND_PREAMBLE: str = (
    "以下是从私域知识库检索到的相关知识，请作为生成代码的参考背景：\n\n"
)

_CONSTRAINTS_PREAMBLE: str = (
    "## 生成约束\n"
)

_CJK_PATTERN = re.compile(
    r'[\u4e00-\u9fff\u3400-\u4dbf'  # CJK统一表意文字 + 扩展A
    r'\u9fa6-\u9fff'                 # CJK统一表意文字（补充）
    r'\u3000-\u303f'                 # CJK标点符号
    r'\uff00-\uffef]'                 # CJK兼容性形式（全角ASCII等）
)


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数。中文按 1.5 char/token，英文按 2.5。"""
    cn_count = len(_CJK_PATTERN.findall(text))
    en_count = len(text) - cn_count
    return int(cn_count / _TOKEN_PER_CHAR_CN + en_count / _TOKEN_PER_CHAR_EN)


def _group_and_sort(items: list[dict]) -> dict[str, list[dict]]:
    """按类型分组，类型内按 score 降序。"""
    groups: dict[str, list[dict]] = {}
    for item in items:
        item_type = item.get("type", "term")
        groups.setdefault(item_type, []).append(item)

    for g in groups.values():
        g.sort(key=lambda x: x.get("score", 0), reverse=True)

    return groups


def _group_key(item_type: str) -> int:
    return _TYPE_ORDER.get(item_type, 999)


def _format_knowledge_item(item: dict, index: int) -> str:
    """格式化单条知识片段为文本。"""
    item_type = item.get("type", "unknown")
    content = item.get("content", "")
    meta = item.get("meta", {})

    type_labels = {
        "term": "术语",
        "api": "API",
        "best_practice": "最佳实践",
        "defect_history": "历史缺陷",
        "security_rule": "安全规则",
        "spec": "规范契约",
        "test_template": "测试模板",
        "document": "文档",
        "spec": "规范契约",
    }
    label = type_labels.get(item_type, item_type)

    source_badge = ""
    knowledge_source = meta.get("knowledge_source", "")
    if knowledge_source == "sdk_code":
        source_badge = " [SDK源码]"
    elif knowledge_source == "doc":
        source_badge = " [文档]"

    lines = [f"### [{label}]{source_badge} (score: {item.get('score', 0):.2f})"]
    lines.append(content)

    code_example = meta.get("code_example", "")
    if code_example:
        lines.append(f"\n示例代码:\n```\n{code_example}\n```")

    related = meta.get("related_entity", "")
    if related:
        lines.append(f"关联实体: {related}")

    lines.append("")
    return "\n".join(lines)


def assemble_sections(
    items: list[dict],
    specs: list[dict],
    user_query: str,
    pinned_knowledge: dict | None,
    role_hint: str,
    max_tokens: int,
    context: dict | None = None,
) -> tuple[PromptSections, AssembleStats]:
    """执行分组排序、裁剪、4段式组装。

    Returns:
        (sections, stats)
    """
    stats = AssembleStats(input_items=len(items))
    role = role_hint or "资深软件工程师"
    token_budget = max_tokens

    # ── System ──
    system_parts = [_SYSTEM_PREAMBLE.format(role_hint=role)]
    spec_lines: list[str] = []
    for spec in specs:
        rule = spec.get("rule", "")
        pos_ex = spec.get("positive_example", "")
        neg_ex = spec.get("negative_example", "")
        line = f"- {rule}"
        if pos_ex:
            line += f"\n  正例: {pos_ex}"
        if neg_ex:
            line += f"\n  反例: {neg_ex}"
        spec_lines.append(line)
    if spec_lines:
        system_parts.append("\n".join(spec_lines))
    else:
        system_parts.append("无特定规范约束。")

    if pinned_knowledge:
        arch = pinned_knowledge.get("architecture_overview", "")
        if arch:
            system_parts.append(f"\n## 架构概览\n{arch}")

    system = "\n".join(system_parts)
    system_tokens = _estimate_tokens(system)

    # ── Constraints ──
    constraint_parts: list[str] = []
    for item in items:
        meta = item.get("meta", {})
        sv = meta.get("since_version", "") or meta.get("version", "")
        dep = meta.get("deprecated_in", "")
        sec = meta.get("security_rule", "")
        if sv:
            constraint_parts.append(f"- 版本要求: {sv}")
        if dep:
            constraint_parts.append(f"- 已废弃于: {dep}")
        if sec:
            constraint_parts.append(f"- 安全要求: {sec}")

    unique_constraints = list(dict.fromkeys(constraint_parts))  # 保序去重
    if context:
        deps = context.get("dependencies", [])
        if deps:
            dep_strs = [f"{d['name']}@{d['version']}" for d in deps]
            unique_constraints.append(f"- 项目依赖: {', '.join(dep_strs)}")

    constraints = _CONSTRAINTS_PREAMBLE + "\n".join(unique_constraints) if unique_constraints else ""
    constraint_tokens = _estimate_tokens(constraints)

    # ── Background ──
    remaining = token_budget - system_tokens - constraint_tokens - 200  # 预留 user 段
    sdk_items = [i for i in items if i.get("meta", {}).get("knowledge_source") == "sdk_code"]
    doc_items = [i for i in items if i.get("meta", {}).get("knowledge_source") != "sdk_code"]

    background_parts = [_BACKGROUND_PREAMBLE]
    current_tokens = _estimate_tokens(_BACKGROUND_PREAMBLE)
    truncated = 0

    # SDK 源码优先（更可靠、更紧凑）
    if sdk_items:
        background_parts.append("### 方法签名（SDK 源码）\n")
        current_tokens += _estimate_tokens(background_parts[-1])
        for i, item in enumerate(sdk_items):
            formatted = _format_knowledge_item(item, i + 1)
            item_tokens = _estimate_tokens(formatted)
            if current_tokens + item_tokens > remaining * 0.8:
                truncated += len(sdk_items) - i
                background_parts.append(f"\n[以下 {truncated} 条知识因 token 预算限制省略]\n")
                break
            background_parts.append(formatted)
            current_tokens += item_tokens

    # 文档内容补充（背景、用法、FAQ）
    if doc_items and truncated == 0:
        background_parts.append("\n### 参考文档\n")
        current_tokens += _estimate_tokens(background_parts[-1])
        groups = _group_and_sort(doc_items)
        for item_type in sorted(groups.keys(), key=_group_key):
            item_list = groups[item_type]
            for i, item in enumerate(item_list):
                formatted = _format_knowledge_item(item, i + 1)
                item_tokens = _estimate_tokens(formatted)
                if current_tokens + item_tokens > remaining * 0.8:
                    truncated += len(item_list) - i
                    for remaining_type in sorted(groups.keys()):
                        if remaining_type > item_type:
                            truncated += len(groups[remaining_type])
                    background_parts.append(
                        f"\n[以下 {truncated} 条知识因 token 预算限制省略]\n"
                    )
                    break
                background_parts.append(formatted)
                current_tokens += item_tokens
            if truncated > 0:
                break

    background = "\n".join(background_parts)

    # ── User Request ──
    user_request = f"## 用户需求\n{user_query}"

    total_estimate = _estimate_tokens(system) + _estimate_tokens(background) + _estimate_tokens(user_request) + _estimate_tokens(constraints)

    stats.after_dedup = len(items)  # 调用方应在传入前完成去重
    stats.after_truncation = max(0, stats.after_dedup - truncated)
    stats.estimated_tokens = total_estimate
    stats.budget_remaining = max(0, token_budget - total_estimate)

    return (
        PromptSections(
            system=system,
            background=background,
            user_request=user_request,
            constraints=constraints,
        ),
        stats,
    )
