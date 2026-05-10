"""Embedding 模型封装 —— 试点期优先 bge-small-zh，降级到零向量占位。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DIM = 768


class EmbeddingModel:
    """向量嵌入模型。延迟加载，不可用时不阻塞。"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh"):
        self.model_name = model_name
        self._model = None
        self._available = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
                self._available = True
                logger.info("embedding model loaded: %s", self.model_name)
            except Exception:
                logger.warning("embedding model unavailable, using zero-vector placeholder")
                self._available = False
        return self._available

    @property
    def dim(self) -> int:
        return _DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量编码文本为向量。不可用时返回零向量。"""
        if not texts:
            return []
        if self.available and self._model is not None:
            embeddings = self._model.encode(texts, normalize_embeddings=True)
            return [e.tolist() for e in embeddings]
        return [[0.0] * self.dim for _ in texts]

    def embed_query(self, query: str) -> list[float]:
        return self.embed([query])[0]


# 全局单例
_embedding_model: EmbeddingModel | None = None


def get_embedding_model() -> EmbeddingModel:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModel()
    return _embedding_model
