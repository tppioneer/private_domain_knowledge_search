"""轻量向量检索 —— 基于 FAISS IndexFlatIP。"""

from __future__ import annotations

import json
import logging
import os

from ....models.schemas import KnowledgeItem, KnowledgeMeta, KnowledgeType, SearchContext

logger = logging.getLogger(__name__)


class LiteVectorSearcher:
    """FAISS 向量检索，内积相似度（与 Milvus IP metric 对齐）。"""

    def __init__(self, index_dir: str = "./data/faiss"):
        self.index_dir = index_dir
        os.makedirs(index_dir, exist_ok=True)
        self._index = None
        self._id_map: dict[int, dict] = {}
        self._next_id = 0
        self._dim = 768
        self._loaded = False

    @property
    def available(self) -> bool:
        return True

    def _load(self):
        if self._loaded:
            return
        index_path = os.path.join(self.index_dir, "vectors.index")
        idmap_path = os.path.join(self.index_dir, "id_map.json")
        try:
            import faiss
        except ImportError:
            self._loaded = True
            return
        if os.path.exists(index_path) and os.path.exists(idmap_path):
            self._index = faiss.read_index(index_path)
            with open(idmap_path, encoding="utf-8") as f:
                raw = json.load(f)
                self._id_map = {int(k): v for k, v in raw.items()}
                self._next_id = max(self._id_map.keys(), default=0) + 1
        else:
            self._index = faiss.IndexFlatIP(self._dim)
        self._loaded = True

    def _save(self):
        import faiss
        index_path = os.path.join(self.index_dir, "vectors.index")
        idmap_path = os.path.join(self.index_dir, "id_map.json")
        faiss.write_index(self._index, index_path)
        with open(idmap_path, "w", encoding="utf-8") as f:
            json.dump(self._id_map, f, ensure_ascii=False)

    def index_vectors(self, ids: list[str], vectors: list[list[float]], metas: list[dict]) -> int:
        """批量索引向量。返回索引数量。"""
        self._load()
        import faiss
        import numpy as np

        vec_array = np.array(vectors, dtype=np.float32)
        for doc_id, meta in zip(ids, metas):
            self._id_map[self._next_id] = {"doc_id": doc_id, **meta}
            self._next_id += 1
        self._index.add(vec_array)
        self._save()
        return len(ids)

    async def search(
        self,
        query: str,
        context: SearchContext | None = None,
        knowledge_types: list[KnowledgeType] | None = None,
        top_k: int = 15,
    ) -> list[KnowledgeItem]:
        self._load()
        if self._index is None or self._index.ntotal == 0:
            return []

        import numpy as np
        from .embedding import get_embedding_model

        emb = get_embedding_model()
        query_vec = np.array([emb.embed_query(query)], dtype=np.float32)

        distances, indices = self._index.search(query_vec, top_k)

        items: list[KnowledgeItem] = []
        allowed_types = {t.value for t in knowledge_types} if knowledge_types else None

        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx not in self._id_map:
                continue
            entry = self._id_map[idx]
            if allowed_types and entry.get("type", "") not in allowed_types:
                continue
            try:
                kt = KnowledgeType(entry.get("type", "api"))
            except ValueError:
                kt = KnowledgeType.API
            items.append(KnowledgeItem(
                id=entry.get("doc_id", str(idx)),
                type=kt,
                content=entry.get("content", ""),
                score=round(float(dist), 4),
                meta=KnowledgeMeta(**{k: v for k, v in entry.items()
                                       if k not in ("doc_id", "type", "content")}),
            ))
        return items
