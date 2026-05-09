"""POST /api/v1/feedback —— 知识反馈上报。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from ..models.schemas import FeedbackRequest, FeedbackResponse

router = APIRouter()


@router.post("/feedback", response_model=FeedbackResponse)
async def record_feedback(request: FeedbackRequest) -> FeedbackResponse:
    # 试点期：仅记录反馈 ID，权重调整逻辑后续实现
    return FeedbackResponse(
        status="recorded",
        feedback_id=f"fb_{uuid.uuid4().hex[:8]}",
    )
