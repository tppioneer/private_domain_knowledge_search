"""Search Service —— 私域知识混合检索微服务。

独立于 MCP Server 运行，通过 HTTP REST 提供知识检索与反馈能力。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .config import service_config
from .api.router import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 全局日志配置
    from .logging_config import setup_service_logging
    setup_service_logging("search_service")
    logging.getLogger(__name__).info("logging configured, log level=%s", os.getenv('LOG_LEVEL', 'INFO'))

    # 启动时预加载 embedding 模型，避免首次请求阻塞
    # 离线环境：设置 EMBEDDING_MODEL_PATH=/path/to/bge-small-zh 指向本地模型
    #            模型不可用时自动降级为零向量，不阻塞启动
    try:
        from .engine.backends.lite.embedding import get_embedding_model
        emb = get_embedding_model()
        if emb.available:
            print(f"[preload] embedding model ready: dim={emb.dim}")
        else:
            print("[preload] embedding model unavailable, using zero-vector placeholder")
    except Exception as e:
        print(f"[preload] embedding model load failed ({e}), using zero-vector placeholder")
    yield


app = FastAPI(
    title="Private Knowledge Search Service",
    version="0.1.0",
    description="私域知识混合检索微服务 —— BM25 + 向量 + 图遍历",
    lifespan=lifespan,
)

app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health():
    from .api.health import health as health_check
    return await health_check()


def main():
    uvicorn.run(
        "search_service.main:app",
        host=service_config.host,
        port=service_config.port,
    )


if __name__ == "__main__":
    uvicorn.run(
        "search_service.main:app",
        host=service_config.host,
        port=service_config.port,
        reload=True,
    )
