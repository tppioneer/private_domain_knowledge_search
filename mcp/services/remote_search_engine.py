"""RemoteSearchEngine —— 通过 HTTP 调用 Search Service 的混合检索。

实现 SearchEngine 抽象接口，将请求转发至 Search Service 的 /api/v1/search。
"""

from __future__ import annotations

import httpx

from ..config import server_config
from ..models.schemas import Context, Diagnostics, KnowledgeItem, KnowledgeMeta, KnowledgeType
from .search_engine import SearchEngine


class RemoteSearchEngine(SearchEngine):
    """通过 HTTP REST 调用 Search Service 的检索实现。"""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or server_config.search_service_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        return self._client

    async def search(
        self,
        query: str,
        context: Context | None = None,
        knowledge_types: list[KnowledgeType] | None = None,
        top_k: int = 5,
        min_score: float = 0.7,
    ) -> tuple[list[KnowledgeItem], Diagnostics]:
        body: dict = {
            "query": query,
            "top_k": top_k,
            "min_score": min_score,
        }
        if context:
            body["context"] = context.model_dump()
        if knowledge_types:
            body["knowledge_types"] = [t.value for t in knowledge_types]

        try:
            resp = await self.client.post(
                f"{self.base_url}/api/v1/search",
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return [], Diagnostics(total_scanned=0, time_ms=0, warnings=["search service unreachable"])

        items = [
            KnowledgeItem(
                id=item["id"],
                type=KnowledgeType(item["type"]),
                content=item["content"],
                score=item["score"],
                meta=KnowledgeMeta(**item.get("meta", {})),
            )
            for item in data.get("items", [])
        ]
        diag_data = data.get("diagnostics", {})
        diagnostics = Diagnostics(
            total_scanned=diag_data.get("total_scanned", 0),
            time_ms=diag_data.get("time_ms", 0),
            warnings=diag_data.get("warnings", []),
        )
        return items, diagnostics
