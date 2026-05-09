"""重排序模块 —— 试点期使用启发式加权，推广期接入 Cross-BERT。"""

from __future__ import annotations

from ..models.schemas import KnowledgeItem

# 知识类型权重（开发场景：API > 最佳实践 > 缺陷历史 > 安全规则）
_TYPE_BOOST: dict[str, float] = {
    "api": 1.0,
    "best_practice": 0.95,
    "defect_history": 0.90,
    "security_rule": 0.85,
    "term": 0.80,
    "spec": 0.95,
    "test_template": 0.75,
}


def rerank(items: list[KnowledgeItem], query: str) -> list[KnowledgeItem]:
    """对融合后的候选集重排序并归一化。

    试点期：启发式加权（类型 boost + 关键词命中加成），
    推广期替换为 Cross-BERT 模型 re-rank。
    """
    if not items:
        return []

    query_lower = query.lower()
    for item in items:
        boost = _TYPE_BOOST.get(item.type.value, 0.5)

        # 关键词命中加成
        content_lower = item.content.lower()
        keyword_hit = sum(1 for word in query_lower.split() if word in content_lower)
        boost += keyword_hit * 0.02

        item.score = round(item.score * boost, 4)

    items.sort(key=lambda x: x.score, reverse=True)

    # 归一化到 [0, 1]
    if items:
        max_score = items[0].score
        if max_score > 1.0:
            for item in items:
                item.score = round(item.score / max_score, 4)

    return items
