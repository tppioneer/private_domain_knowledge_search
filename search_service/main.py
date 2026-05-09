"""Search Service —— 私域知识混合检索微服务。

独立于 MCP Server 运行，通过 HTTP REST 提供知识检索与反馈能力。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .config import service_config
from .api.router import router


@asynccontextmanager
async def lifespan(app: FastAPI):
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
