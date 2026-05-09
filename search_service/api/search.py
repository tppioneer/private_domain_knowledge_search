"""POST /api/v1/search —— 混合检索。"""

from __future__ import annotations

import time

from fastapi import APIRouter

from ..models.schemas import Diagnostics, KnowledgeItem, KnowledgeMeta, KnowledgeType, SearchRequest, SearchResponse

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    start = time.perf_counter()

    # 试点期：尝试真实引擎，不可用或无结果时降级到 mock
    try:
        from ..engine.hybrid_searcher import HybridSearcher
        searcher = HybridSearcher()
        items, diagnostics = await searcher.search(
            query=request.query,
            context=request.context,
            knowledge_types=request.knowledge_types,
            top_k=request.top_k,
            min_score=request.min_score,
        )
        if not items:
            items, diagnostics = _mock_search(request)
            diagnostics.warnings.append("engine returned no results, fallback to mock")
    except Exception:
        items, diagnostics = _mock_search(request)
        diagnostics.warnings.append("engine unavailable, fallback to mock")

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    diagnostics.time_ms = elapsed_ms

    return SearchResponse(items=items, diagnostics=diagnostics)


def _mock_search(request: SearchRequest) -> tuple[list[KnowledgeItem], Diagnostics]:
    """演示用 mock 数据，与设计文档附录中积分服务场景对齐。"""
    items = [
        KnowledgeItem(
            id="sdk_doc_101",
            type=KnowledgeType.API,
            content="points-sdk v2.3 DeductPoints(bizId, uid, points) 返回 (changeId, error)。需传入业务幂等键。",
            score=0.96,
            meta=KnowledgeMeta(
                sdk="points-sdk",
                version="v2.3",
                code_example="changeId, err := sdk.DeductPoints(bizId, uid, points)",
                related_entity="PointsService",
                config_required="NewPointsClient(appKey, secret)",
            ),
        ),
        KnowledgeItem(
            id="best_prac_206",
            type=KnowledgeType.BEST_PRACTICE,
            content="积分扣减幂等处理：使用 bizId+uid+eventType 作为幂等键，调用前检查流水。",
            score=0.93,
            meta=KnowledgeMeta(
                source="team_retro_session_2026-03",
                applicable_version="*",
            ),
        ),
        KnowledgeItem(
            id="defect_hist_89",
            type=KnowledgeType.DEFECT_HISTORY,
            content="历史缺陷：积分扣减未包在本地事务中导致少扣，必须开启DB事务并加SDK重试。",
            score=0.89,
            meta=KnowledgeMeta(
                related_ticket="INC-4829",
            ),
        ),
    ]

    diag = Diagnostics(
        total_scanned=3400,
        warnings=["search engine unavailable, returned mock data"],
    )
    return items, diag
