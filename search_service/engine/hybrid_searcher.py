"""混合检索编排 —— BM25 + 向量 + 图遍历 → 三路融合 → 重排序。"""

from __future__ import annotations

import asyncio

from ..config import service_config
from ..models.schemas import Diagnostics, KnowledgeItem, KnowledgeType, SearchContext
from .factory import create_bm25_searcher, create_vector_searcher, create_graph_searcher
from .ranker import rerank


class HybridSearcher:
    """混合检索引擎。

    流水线: BM25 | 向量 | 图 → 合并去重 → 重排序 → Top-K 裁剪
    """

    def __init__(self):
        self.bm25 = create_bm25_searcher()
        self.vector = create_vector_searcher()
        self.graph = create_graph_searcher()

    async def search(
        self,
        query: str,
        context: SearchContext | None = None,
        knowledge_types: list[KnowledgeType] | None = None,
        top_k: int = 5,
        min_score: float = 0.7,
    ) -> tuple[list[KnowledgeItem], Diagnostics]:
        candidate_k = top_k * service_config.candidate_multiplier
        warnings: list[str] = []

        # ── 三路并行检索 ──
        bm25_task = _safe_search(
            self.bm25, "bm25", query, context, knowledge_types, candidate_k, warnings,
        )
        vector_task = _safe_search(
            self.vector, "vector", query, context, knowledge_types, candidate_k, warnings,
        )
        graph_task = _safe_search(
            self.graph, "graph", query, context, knowledge_types, candidate_k, warnings,
        )

        bm25_results, vector_results, graph_results = await asyncio.gather(
            bm25_task, vector_task, graph_task,
        )

        # ── 三路融合 + 去重 ──
        merged: dict[str, KnowledgeItem] = {}
        total_scanned = 0
        for results in (bm25_results, vector_results, graph_results):
            for item in results:
                total_scanned += 1
                if item.id not in merged:
                    merged[item.id] = item
                else:
                    # 保留最高分
                    if item.score > merged[item.id].score:
                        merged[item.id] = item

        fused = list(merged.values())

        # ── 重排序 ──
        ranked = rerank(fused, query)

        # ── 裁剪 ──
        filtered = [item for item in ranked if item.score >= min_score][:top_k]

        diagnostics = Diagnostics(
            total_scanned=total_scanned,
            time_ms=0,  # 调用方填充
            warnings=warnings,
        )
        return filtered, diagnostics


async def _safe_search(
    searcher,
    name: str,
    query: str,
    context: SearchContext | None,
    knowledge_types: list[KnowledgeType] | None,
    top_k: int,
    warnings: list[str],
) -> list[KnowledgeItem]:
    """安全调用单个检索器，失败时返回空并记录警告。"""
    try:
        results = await asyncio.wait_for(
            searcher.search(query, context, knowledge_types, top_k),
            timeout=service_config.search_timeout_ms / 1000,
        )
        return results or []
    except asyncio.TimeoutError:
        warnings.append(f"{name} search timed out")
        return []
    except Exception as e:
        warnings.append(f"{name} search unavailable: {e}")
        return []
