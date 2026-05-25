"""重排序模块 —— 启发式加权 + 条件层级提升（方案二）。"""

from __future__ import annotations

from ..models.schemas import KnowledgeItem, LayeredRecommendation

# 知识类型权重（开发场景：API > 最佳实践 > 缺陷历史 > 安全规则）
_TYPE_BOOST: dict[str, float] = {
    "api": 1.0,
    "best_practice": 0.95,
    "defect_history": 0.90,
    "security_rule": 0.85,
    "term": 0.80,
    "spec": 0.95,
    "test_template": 0.75,
    "document": 0.90,
}

# SDK 角色权重（入口 > 公开 API > 内部实现）
_ROLE_BOOST: dict[str, float] = {
    "entry_point": 1.0,
    "public_api": 0.92,
    "internal": 0.80,
}

# 层级加权（条件触发时的调整比例）
_LAYER_BOOST: dict[str, float] = {
    "high": 1.30,   # +30% — 有替代关系的高层推荐
    "mid": 1.0,      # 不变
    "low": 0.80,     # -20% — 低层 API 降权（存在高层替代时）
}

# 有 standard_context_provider 时的额外加成
_CONTEXT_PROVIDER_BOOST = 1.10  # +10%

# 条件层级提升的语义分数阈值
_SEMANTIC_THRESHOLD = 0.55


def _module_overlap(item: KnowledgeItem, query: str) -> bool:
    """快速领域一致性检查：查询词是否与 item 的模块/包名有交集。"""
    meta = item.meta
    if not meta:
        return False
    module = (meta.sdk or "").lower()
    sdk_class = (meta.sdk_class or "").lower()
    query_lower = query.lower()

    # 模块名或类名中的关键词是否出现在查询中
    if module:
        parts = module.replace("-", " ").replace("_", " ").split()
        for p in parts:
            if len(p) > 2 and p in query_lower:
                return True
    if sdk_class:
        # 提取包路径的关键部分
        pkg_parts = sdk_class.split(".")
        for part in pkg_parts:
            if len(part) > 2 and part in query_lower:
                return True
    return False


def _should_apply_layer_boost(item: KnowledgeItem, query: str, threshold: float) -> bool:
    """判断是否触发条件层级提升。

    条件：
    1. item.score > threshold（语义相似度达标）
    2. 通过快速领域一致性检查
    """
    if item.score < threshold:
        return False
    return _module_overlap(item, query)


def _build_layered_recommendations(items: list[KnowledgeItem]) -> list[LayeredRecommendation]:
    """从已排序的 items 构建顶层分层推荐列表。

    将 high-layer 或存在 suggested_alternative 的 item 提炼为独立推荐项。
    """
    recommendations: list[LayeredRecommendation] = []
    for item in items:
        meta = item.meta
        if not meta:
            continue
        layer = meta.layer or "mid"
        alt = meta.suggested_alternative

        if layer == "high" or (alt and alt.full_call_chain):
            rec = LayeredRecommendation(
                layer=layer,
                score=item.score,
                content=alt.full_call_chain if alt and alt.full_call_chain
                        else f"{meta.sdk_class or ''}.{meta.method or ''}",
                description=alt.description if alt else "",
                full_call_chain=alt.full_call_chain if alt else "",
                entry_method=alt.entry_method if alt else "",
            )
            recommendations.append(rec)

    return recommendations


def rerank(
    items: list[KnowledgeItem],
    query: str,
    query_context_module: str | None = None,
) -> tuple[list[KnowledgeItem], list[LayeredRecommendation]]:
    """对融合后的候选集重排序并归一化。

    流程：
    1. 基础加权：类型 boost × 角色 boost
    2. 关键词命中加成
    3. 条件层级提升（语义达标 + 领域一致时触发）
    4. 归一化到 [0, 1]
    5. 构建分层推荐列表
    """
    if not items:
        return [], []

    query_lower = query.lower()
    for item in items:
        boost = _TYPE_BOOST.get(item.type.value, 0.5)

        # 角色加成（SDK 入口 > 公开 API > 内部实现）
        role = item.meta.role if item.meta and item.meta.role else ""
        role_boost = _ROLE_BOOST.get(role, 0.90)
        boost *= role_boost

        # 关键词命中加成
        content_lower = item.content.lower()
        keyword_hit = sum(1 for word in query_lower.split() if word in content_lower)
        boost += keyword_hit * 0.02

        # 条件层级提升
        if _should_apply_layer_boost(item, query, _SEMANTIC_THRESHOLD):
            layer = item.meta.layer if item.meta and item.meta.layer else "mid"

            # Low-Level：有高层替代时才降权
            if layer == "low" and item.meta and item.meta.suggested_alternative:
                boost *= _LAYER_BOOST["low"]
            # High-Level：始终加权
            elif layer == "high":
                boost *= _LAYER_BOOST["high"]

            # 有 standard_context_provider 加成
            if item.meta and item.meta.standard_context_provider:
                boost *= _CONTEXT_PROVIDER_BOOST

        item.score = round(item.score * boost, 4)

    items.sort(key=lambda x: x.score, reverse=True)

    # 归一化到 [0, 1]
    if items:
        max_score = items[0].score
        if max_score > 1.0:
            for item in items:
                item.score = round(item.score / max_score, 4)

    recommendations = _build_layered_recommendations(items)

    return items, recommendations
