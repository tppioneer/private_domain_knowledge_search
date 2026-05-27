"""POST /api/v1/search —— 混合检索。"""

from __future__ import annotations

import time

from fastapi import APIRouter

from ..models.schemas import Diagnostics, KnowledgeItem, KnowledgeMeta, KnowledgeType, SearchRequest, SearchResponse

router = APIRouter()

# 无需展开返回值的类型
_SKIP_RETURN_TYPES = {
    "void", "int", "long", "float", "double", "boolean", "byte", "short", "char",
    "String", "Object", "Integer", "Long", "Float", "Double", "Boolean", "Byte", "Short",
    "Number", "Class", "List", "Map", "Set", "Collection",
}


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    start = time.perf_counter()

    # 试点期：尝试真实引擎，不可用或无结果时降级到 mock
    try:
        from ..engine.hybrid_searcher import HybridSearcher
        searcher = HybridSearcher()
        items, diagnostics, layered_recs = await searcher.search(
            query=request.query,
            context=request.context,
            knowledge_types=request.knowledge_types,
            top_k=request.top_k,
            min_score=request.min_score,
        )
        if not items:
            items, diagnostics = _mock_search(request)
            diagnostics.warnings.append("engine returned no results, fallback to mock")
            layered_recs = []
        else:
            # 展开 entry_point 返回值类型的方法链
            expanded = _expand_return_chain(items, top_k=request.top_k)
            if expanded:
                items = items + expanded
    except Exception:
        items, diagnostics = _mock_search(request)
        diagnostics.warnings.append("engine unavailable, fallback to mock")
        layered_recs = []

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    diagnostics.time_ms = elapsed_ms

    return SearchResponse(items=items, diagnostics=diagnostics, layered_recommendations=layered_recs)


def _expand_return_chain(
    items: list[KnowledgeItem], top_k: int = 5,
) -> list[KnowledgeItem]:
    """展开 entry_point 方法的返回值类型方法。

    例如: FileServiceManager.getSystemService() → FileService
          自动补充 FileService.uploadFile/downloadFile 等方法到结果中。
    """
    entry_types: dict[str, float] = {}  # return_type → best_score
    for item in items:
        if not item.meta or not item.meta.role:
            continue
        if item.meta.role != "entry_point":
            continue
        rt = (item.meta.return_type or "").strip()
        if not rt or rt in _SKIP_RETURN_TYPES:
            continue
        if rt not in entry_types or item.score > entry_types[rt]:
            entry_types[rt] = item.score

    if not entry_types:
        return []

    try:
        from ..engine.backends.lite.entity import LiteEntitySearcher
        entity_searcher = LiteEntitySearcher()
        if not entity_searcher.available:
            return []
    except Exception:
        return []

    existing_ids = {item.id for item in items}
    expanded: list[KnowledgeItem] = []

    for return_type, parent_score in entry_types.items():
        candidates = entity_searcher.search_entity(return_type)
        added = 0
        for c in candidates:
            chunk = c["chunk"]
            cid = chunk["id"]
            if cid in existing_ids:
                continue
            existing_ids.add(cid)
            meta = chunk.get("meta", {})
            try:
                kt = KnowledgeType(chunk.get("type", "api"))
            except ValueError:
                kt = KnowledgeType.API
            expanded.append(KnowledgeItem(
                id=cid,
                type=kt,
                content=chunk.get("content", ""),
                score=round(parent_score * 0.92, 4),  # 间接相关，略低于入口
                meta=KnowledgeMeta(**meta),
            ))
            added += 1
            if added >= top_k:
                break

    return expanded


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
