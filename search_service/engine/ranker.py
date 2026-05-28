"""重排序模块 —— 角色/层级加权 + 入口方法加成（方案三）。

策略变化：
- 角色和层级 boost 无条件生效，不再需要语义阈值验证
- 高低层差距从 1.6x 扩大到 5x，确保通用查询优先返回入口方法
- 入口方法有 return_type 时额外加成（gateway 信号）
"""

from __future__ import annotations

from ..models.schemas import KnowledgeItem, LayeredRecommendation

# 知识类型权重
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

# SDK 角色权重 —— entry_point vs internal 差距 1.8x
_ROLE_BOOST: dict[str, float] = {
    "entry_point": 1.0,
    "public_api": 0.80,
    "internal": 0.55,
}

# 层级加权 —— high vs low 差距 2.7x，suppressed 几乎不可见
_LAYER_BOOST: dict[str, float] = {
    "high": 1.50,
    "mid": 1.0,
    "low": 0.55,
    "suppressed": 0.10,
}

# 入口方法有返回值类型时额外加成（gateway 信号）
_GATEWAY_BOOST = 1.15

# 构造入口方法加成 —— builder/static_factory/singleton/constructor 排到同类方法最前面
_CONSTRUCTION_BOOST = 1.25

# 无需展开返回值的类型
_SKIP_RETURN_TYPES = {
    "void", "int", "long", "float", "double", "boolean", "byte", "short", "char",
    "String", "Object", "Integer", "Long", "Float", "Double", "Boolean", "Byte", "Short",
    "Number", "Class", "List", "Map", "Set", "Collection",
}


def _build_layered_recommendations(items: list[KnowledgeItem]) -> list[LayeredRecommendation]:
    """从已排序的 items 构建顶层分层推荐列表。"""
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

    1. 类型 boost × 角色 boost × 层级 boost（无条件生效）
    2. 关键字命中加成（与 base score 比例缩放）
    3. 入口 gateway 加成（有 return_type 的 entry_point）
    4. 归一化到 [0, 1]
    """
    if not items:
        return [], []

    query_lower = query.lower()
    query_words = [w for w in query_lower.split() if len(w) > 1]

    for item in items:
        boost = _TYPE_BOOST.get(item.type.value, 0.5)

        # 角色加成
        role = item.meta.role if item.meta and item.meta.role else ""
        boost *= _ROLE_BOOST.get(role, 0.80)

        # 层级加权（无条件）
        layer = item.meta.layer if item.meta and item.meta.layer else "mid"
        layer_mult = _LAYER_BOOST.get(layer, 1.0)
        # low 层没有高层替代时惩罚减半（可能是合理的底层查询）
        if layer == "low" and not (item.meta and item.meta.suggested_alternative):
            layer_mult = 0.78
        boost *= layer_mult

        # 关键字命中（与 base 比例缩放，而非固定加法）
        content_lower = item.content.lower()
        hit_count = sum(1 for w in query_words if w in content_lower)
        if hit_count:
            boost *= 1.0 + hit_count * 0.03

        # 入口 gateway 加成
        if role == "entry_point":
            rt = (item.meta.return_type or "").strip() if item.meta else ""
            if rt and rt not in _SKIP_RETURN_TYPES:
                boost *= _GATEWAY_BOOST

        # 构造入口加成 —— newBuilder/getInstance 等方法排最前
        if item.meta and item.meta.construction_pattern:
            boost *= _CONSTRUCTION_BOOST

        item.score = round(item.score * boost, 4)

    items.sort(key=lambda x: x.score, reverse=True)

    # 归一化
    if items and items[0].score > 1.0:
        max_score = items[0].score
        for item in items:
            item.score = round(item.score / max_score, 4)

    recommendations = _build_layered_recommendations(items)

    return items, recommendations
