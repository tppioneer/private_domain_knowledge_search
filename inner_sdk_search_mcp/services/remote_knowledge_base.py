"""RemoteKnowledgeBase —— 通过 HTTP 调用 Search Service 的知识库接口。"""

from __future__ import annotations

import logging

import httpx

from ..config import server_config
from ..models.schemas import (
    EntityDetailResponse,
    EntityType,
    PinnedKnowledge,
    ProjectMeta,
    ReportFeedbackResponse,
    SpecItem,
)
from .knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


def _log_error(operation: str, url: str, exc: Exception, detail: str = ""):
    """统一错误日志：区分连接、超时、HTTP 状态码、未知异常。"""
    if isinstance(exc, httpx.ConnectError):
        logger.error("search service unreachable: %s %s", operation, url)
    elif isinstance(exc, httpx.TimeoutException):
        logger.error("search service timeout: %s %s %s", operation, url, detail)
    elif isinstance(exc, httpx.HTTPStatusError):
        logger.error(
            "search service HTTP %d: %s %s %s",
            exc.response.status_code, operation, url,
            exc.response.text[:200] if exc.response.text else "",
        )
    else:
        logger.exception("search service unexpected error: %s %s %s", operation, url, detail)


class RemoteKnowledgeBase(KnowledgeBase):
    """通过 HTTP REST 调用 Search Service 的知识库实现。"""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or server_config.search_service_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        return self._client

    async def get_entity(
        self,
        entity_name: str,
        entity_type: EntityType | None = None,
        version_requirement: str | None = None,
    ) -> EntityDetailResponse | None:
        url = f"{self.base_url}/api/v1/entities/{entity_name}"
        params: dict[str, str] = {}
        if entity_type:
            params["entity_type"] = entity_type.value
        if version_requirement:
            params["version_requirement"] = version_requirement

        try:
            resp = await self.client.get(url, params=params)
            data = resp.json()
            logger.info("entity found: name=%s type=%s", entity_name, data.get("entity_type", ""))
            return EntityDetailResponse(**data)
        except Exception as e:
            _log_error("get_entity", url, e)
            return None

    async def get_specs(
        self,
        module: str | None = None,
        file_path: str | None = None,
        dependency_constraints: dict[str, str] | None = None,
    ) -> tuple[list[SpecItem], list[str]]:
        url = f"{self.base_url}/api/v1/specs"
        params: dict[str, str] = {}
        if module:
            params["module"] = module
        if file_path:
            params["file_path"] = file_path

        try:
            resp = await self.client.get(url, params=params)
            data = resp.json()
            specs = [SpecItem(**s) for s in data.get("specs", [])]
            warnings = data.get("conflict_warnings", [])
            logger.info("specs loaded: module=%s count=%d", module or "all", len(specs))
            return specs, warnings
        except Exception as e:
            _log_error("get_specs", url, e, f"module={module}")
            return [], []

    async def recommend(self, project_meta: ProjectMeta) -> PinnedKnowledge:
        url = f"{self.base_url}/api/v1/recommend"
        try:
            resp = await self.client.post(url, json=project_meta.model_dump())
            data = resp.json()
            pk = data.get("pinned_knowledge", {})
            logger.info(
                "recommend loaded: project=%s apis=%d specs=%d",
                project_meta.project_id,
                len(pk.get("common_apis", [])),
                len(pk.get("recent_spec_updates", [])),
            )
            return PinnedKnowledge(**pk)
        except Exception as e:
            _log_error("recommend", url, e, f"project={project_meta.project_id}")
            return PinnedKnowledge()

    async def record_feedback(
        self,
        session_id: str,
        consumed_knowledge_ids: list[str],
        action: str,
        modification_detail: dict | None = None,
    ) -> ReportFeedbackResponse:
        url = f"{self.base_url}/api/v1/feedback"
        body: dict = {
            "session_id": session_id,
            "consumed_knowledge_ids": consumed_knowledge_ids,
            "action": action,
        }
        if modification_detail:
            body["modification_detail"] = modification_detail

        try:
            resp = await self.client.post(url, json=body)
            data = resp.json()
            logger.info(
                "feedback recorded: session=%s action=%s ids=%d feedback_id=%s",
                session_id, action, len(consumed_knowledge_ids), data.get("feedback_id", ""),
            )
            return ReportFeedbackResponse(**data)
        except Exception as e:
            _log_error("record_feedback", url, e, f"session={session_id} action={action}")
            return ReportFeedbackResponse(status="failed", feedback_id="")
