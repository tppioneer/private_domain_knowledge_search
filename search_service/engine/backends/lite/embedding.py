"""Embedding 模型封装 —— 试点期优先 bge-small-zh，降级到零向量占位。

离线环境: 设置 EMBEDDING_MODEL_PATH=/path/to/bge-small-zh 指向本地模型目录。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# 缩短 HF 下载超时，使离线环境下 SentenceTransformer 快速失败（默认无超时 → 每重试 10s+）
if "HF_HUB_DOWNLOAD_TIMEOUT" not in os.environ:
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "5"

_DIM = 768


class EmbeddingModel:
    """向量嵌入模型。延迟加载，不可用时不阻塞。"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh", model_path: str = ""):
        self.model_name = model_name
        self.model_path = model_path  # 本地路径优先，离线环境使用
        self._model = None
        self._available = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                from sentence_transformers import SentenceTransformer
                source = self.model_path or self.model_name
                if self.model_path:
                    logger.info("loading embedding model from local path: %s", source)
                self._model = SentenceTransformer(source)
                self._available = True
                logger.info("embedding model loaded: %s (dim=%d)", source, self.dim)
            except Exception:
                logger.warning(
                    "embedding model unavailable (%s), using zero-vector placeholder. "
                    "Set EMBEDDING_MODEL_PATH to a local model directory for offline use.",
                    self.model_path or self.model_name,
                )
                self._available = False
        return self._available

    @property
    def dim(self) -> int:
        if self._model is not None:
            return self._model.get_sentence_embedding_dimension() or _DIM  # noqa — compat with older versions
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
        from ....config import service_config
        _embedding_model = EmbeddingModel(
            model_name=service_config.embedding_model_name,
            model_path=service_config.embedding_model_path,
        )
    return _embedding_model
