"""知识片段去重 —— 高效去重（ID精确去重 + 近似相似度去重）。

优化策略：
1. 按 score 降序排序，确保高相关性结果优先保留
2. 限制相似度比较范围为 Top-N，避免 O(n²) 复杂度
3. 内容长度预过滤，差异过大直接跳过
4. 快速哈希初筛，减少实际相似度计算次数
"""

from __future__ import annotations

import hashlib
from difflib import SequenceMatcher

_SIMILARITY_THRESHOLD = 0.92
_DEFAULT_TOP_N = 50
_LENGTH_RATIO_THRESHOLD = 2.0


def _quick_hash(text: str, n: int = 50) -> str:
    """生成文本的快速哈希，用于初筛明显不同的内容。"""
    return hashlib.md5(text.encode()).hexdigest()[:n]


def deduplicate_items(items: list[dict], top_n: int = _DEFAULT_TOP_N) -> list[dict]:
    """高效去重并保留 score 更高者。

    规则:
    1. 同ID只保留第一个
    2. 内容相似度 > 0.92 的保留 score 高者

    复杂度优化:
    - 按 score 降序排序
    - 只与已保留结果的前 top_n 条比较
    - 长度差异过大时跳过

    Args:
        items: 知识片段列表
        top_n: 相似度比较的范围上限，默认50

    Returns:
        去重后的知识片段列表（按 score 降序排列）
    """
    if len(items) <= 1:
        return list(items)

    sorted_items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)

    seen_ids: set[str] = set()
    id_filtered: list[dict] = []
    for item in sorted_items:
        iid = item.get("id", "")
        if iid not in seen_ids:
            seen_ids.add(iid)
            id_filtered.append(item)

    result: list[dict] = []
    result_hashes: list[str] = []

    for item in id_filtered:
        item_content = item.get("content", "").lower()
        item_len = len(item_content)

        if item_len == 0:
            result.append(item)
            result_hashes.append("")
            continue

        duplicate = False
        item_hash = _quick_hash(item_content, 20)

        for i, kept in enumerate(result):
            if i >= top_n:
                break

            kept_content = kept.get("content", "").lower()
            kept_len = len(kept_content)

            if kept_len == 0:
                continue

            if _LENGTH_RATIO_THRESHOLD < item_len / kept_len or _LENGTH_RATIO_THRESHOLD < kept_len / item_len:
                continue

            kept_hash = result_hashes[i]
            if kept_hash and item_hash != kept_hash and item_hash[:10] != kept_hash[:10]:
                continue

            sim = SequenceMatcher(None, item_content, kept_content).ratio()
            if sim >= _SIMILARITY_THRESHOLD:
                if item.get("score", 0) > kept.get("score", 0):
                    result[i] = item
                    result_hashes[i] = item_hash
                duplicate = True
                break

        if not duplicate:
            result.append(item)
            result_hashes.append(item_hash)

    return result
