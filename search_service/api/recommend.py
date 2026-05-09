"""POST /api/v1/recommend —— 项目上下文预加载。"""

from __future__ import annotations

from fastapi import APIRouter

from ..models.schemas import CommonApi, PinnedKnowledge, ProjectMeta, RecentSpecUpdate, RecommendResponse

router = APIRouter()


@router.post("/recommend", response_model=RecommendResponse)
async def recommend(request: ProjectMeta) -> RecommendResponse:
    # 试点期 mock：按设计文档订单系统场景
    pinned = PinnedKnowledge(
        architecture_overview="订单系统整体采用DDD架构，积分服务位于 infrastructure 层...",
        common_apis=[
            CommonApi(name="DeductPoints", snippet="DeductPoints(bizId, uid, points) (changeId, error)"),
            CommonApi(name="GamClient.callback", snippet="callback(code) UserInfoResponse"),
        ],
        recent_spec_updates=[
            RecentSpecUpdate(rule="订单回调幂等必须使用分布式锁", updated_at="2026-05-01"),
        ],
        security_whitelist_patterns=["/auth/callback", "/points/inner-callback"],
    )
    return RecommendResponse(pinned_knowledge=pinned)
