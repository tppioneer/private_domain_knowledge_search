"""知识库访问接口 —— 实体查询、规范获取、上下文推荐、反馈记录。

当前提供 Mock 实现，后续替换为对接向量库+ES+图数据库的真实实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.schemas import (
    ConflictWarning,
    EntityDetailResponse,
    EntityType,
    ListReposResponse,
    PinnedKnowledge,
    ProjectMeta,
    ReportFeedbackResponse,
    RepoItem,
    SpecItem,
)


class KnowledgeBase(ABC):
    """知识库抽象接口。"""

    @abstractmethod
    async def get_entity(
        self,
        entity_name: str,
        entity_type: EntityType | None = None,
        version_requirement: str | None = None,
    ) -> EntityDetailResponse | None:
        ...

    @abstractmethod
    async def get_specs(
        self,
        module: str | None = None,
        file_path: str | None = None,
        dependency_constraints: dict[str, str] | None = None,
    ) -> tuple[list[SpecItem], list[str]]:
        ...

    @abstractmethod
    async def recommend(self, project_meta: ProjectMeta) -> PinnedKnowledge:
        ...

    @abstractmethod
    async def record_feedback(
        self,
        session_id: str,
        consumed_knowledge_ids: list[str],
        action: str,
        modification_detail: dict | None = None,
    ) -> ReportFeedbackResponse:
        ...

    @abstractmethod
    async def list_repos(self) -> ListReposResponse:
        ...


class MockKnowledgeBase(KnowledgeBase):
    """Mock 实现，用于开发和测试。"""

    async def get_entity(
        self,
        entity_name: str,
        entity_type: EntityType | None = None,
        version_requirement: str | None = None,
    ) -> EntityDetailResponse | None:
        return None

    async def get_specs(
        self,
        module: str | None = None,
        file_path: str | None = None,
        dependency_constraints: dict[str, str] | None = None,
    ) -> tuple[list[SpecItem], list[str]]:
        return [], []

    async def recommend(self, project_meta: ProjectMeta) -> PinnedKnowledge:
        return PinnedKnowledge()

    async def record_feedback(
        self,
        session_id: str,
        consumed_knowledge_ids: list[str],
        action: str,
        modification_detail: dict | None = None,
    ) -> ReportFeedbackResponse:
        import uuid
        return ReportFeedbackResponse(
            status="recorded",
            feedback_id=f"fb_{uuid.uuid4().hex[:8]}",
        )

    async def list_repos(self) -> ListReposResponse:
        # Mock 实现：尝试从本地 registry.json 读取
        import json, os
        reg_path = os.path.join(os.getcwd(), "data", "repos", "registry.json")
        if os.path.exists(reg_path):
            try:
                with open(reg_path, encoding="utf-8") as f:
                    registry = json.load(f)
                repos = []
                for name, info in registry.get("repos", {}).items():
                    repos.append(RepoItem(
                        name=name, path=info.get("path", ""),
                        chunk_count=info.get("chunk_count", 0),
                        last_indexed=info.get("last_indexed", ""),
                    ))
                return ListReposResponse(repos=repos, total=len(repos))
            except Exception:
                pass
        return ListReposResponse()
