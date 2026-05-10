"""RemoteKnowledgeBase —— 通过 HTTP 调用 Search Service 的知识库接口。

实现 KnowledgeBase 抽象接口，将调用转发至 Search Service。
"""

from __future__ import annotations

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
        params: dict[str, str] = {}
        if entity_type:
            params["entity_type"] = entity_type.value
        if version_requirement:
            params["version_requirement"] = version_requirement

        try:
            resp = await self.client.get(
                f"{self.base_url}/api/v1/entities/{entity_name}",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            return EntityDetailResponse(**data)
        except Exception:
            return None

    async def get_specs(
        self,
        module: str | None = None,
        file_path: str | None = None,
        dependency_constraints: dict[str, str] | None = None,
    ) -> tuple[list[SpecItem], list[str]]:
        params: dict[str, str] = {}
        if module:
            params["module"] = module
        if file_path:
            params["file_path"] = file_path

        try:
            resp = await self.client.get(
                f"{self.base_url}/api/v1/specs",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            specs = [SpecItem(**s) for s in data.get("specs", [])]
            warnings = data.get("conflict_warnings", [])
            return specs, warnings
        except Exception:
            return [], []

    async def recommend(self, project_meta: ProjectMeta) -> PinnedKnowledge:
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/v1/recommend",
                json=project_meta.model_dump(),
            )
            resp.raise_for_status()
            data = resp.json()
            return PinnedKnowledge(**data.get("pinned_knowledge", {}))
        except Exception:
            return PinnedKnowledge()

    async def record_feedback(
        self,
        session_id: str,
        consumed_knowledge_ids: list[str],
        action: str,
        modification_detail: dict | None = None,
    ) -> ReportFeedbackResponse:
        body: dict = {
            "session_id": session_id,
            "consumed_knowledge_ids": consumed_knowledge_ids,
            "action": action,
        }
        if modification_detail:
            body["modification_detail"] = modification_detail

        try:
            resp = await self.client.post(
                f"{self.base_url}/api/v1/feedback",
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return ReportFeedbackResponse(**data)
        except Exception:
            return ReportFeedbackResponse(status="failed", feedback_id="")
