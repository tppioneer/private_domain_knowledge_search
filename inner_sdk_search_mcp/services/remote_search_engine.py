"""RemoteSearchEngine —— 通过 HTTP 调用 Search Service 的混合检索。"""

from __future__ import annotations

import logging

import httpx

from ..config import server_config
from ..models.schemas import Context, Diagnostics, KnowledgeItem, KnowledgeMeta, KnowledgeType
from .search_engine import SearchEngine

logger = logging.getLogger(__name__)


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
        url = f"{self.base_url}/api/v1/search"
        body: dict = {
            "query": query,
            "top_k": top_k,
            "min_score": min_score,
        }
        if context:
            body["context"] = context.model_dump()
        if knowledge_types:
            body["knowledge_types"] = [t.value for t in knowledge_types]

        logger.debug("remote search: url=%s query=%s top_k=%d", url, query[:100], top_k)

        try:
            resp = await self.client.post(url, json=body)
        except httpx.ConnectError:
            logger.error("search service unreachable: %s", url)
            return [], Diagnostics(total_scanned=0, time_ms=0, warnings=["search service unreachable: connection refused"])
        except httpx.TimeoutException:
            logger.error("search service timeout: %s query=%s", url, query[:100])
            return [], Diagnostics(total_scanned=0, time_ms=0, warnings=["search service timeout"])
        except httpx.HTTPStatusError as e:
            logger.error(
                "search service HTTP %d: %s query=%s response=%s",
                e.response.status_code, url, query[:100],
                e.response.text[:200] if e.response.text else "",
            )
            return [], Diagnostics(total_scanned=0, time_ms=0,
                                    warnings=[f"search service returned HTTP {e.response.status_code}"])
        except Exception:
            logger.exception("search service unexpected error: %s query=%s", url, query[:100])
            return [], Diagnostics(total_scanned=0, time_ms=0, warnings=["search service error"])

        data = resp.json()
        item_count = len(data.get("items", []))
        diag = data.get("diagnostics", {})
        logger.info(
            "remote search: query=%s items=%d time=%dms scanned=%d",
            query[:100], item_count, diag.get("time_ms", 0), diag.get("total_scanned", 0),
        )

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
        diagnostics = Diagnostics(
            total_scanned=diag.get("total_scanned", 0),
            time_ms=diag.get("time_ms", 0),
            warnings=diag.get("warnings", []),
        )
        return items, diagnostics
