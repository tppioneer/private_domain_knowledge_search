"""report_feedback —— 知识反馈上报。"""

from __future__ import annotations

from ..models.schemas import FeedbackAction, ModificationDetail, ReportFeedbackResponse
from ..services.knowledge_base import KnowledgeBase


async def report_feedback(
    kb: KnowledgeBase,
    session_id: str,
    consumed_knowledge_ids: list[str],
    action: FeedbackAction,
    modification_detail: ModificationDetail | None = None,
) -> ReportFeedbackResponse:
    return await kb.record_feedback(
        session_id=session_id,
        consumed_knowledge_ids=consumed_knowledge_ids,
        action=action.value,
        modification_detail=modification_detail.model_dump() if modification_detail else None,
    )
