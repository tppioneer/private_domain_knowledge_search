"""MCP Server 配置。"""

from __future__ import annotations

import os


class ServerConfig:
    name: str = "private-knowledge-mcp"
    version: str = "0.2.0"
    description: str = "私域知识自动检索 MCP Server —— 为 AI Coding 工具提供企业私域知识检索能力"

    search_timeout_ms: int = int(os.getenv("SEARCH_TIMEOUT_MS", "300"))

    # ── Search Service 远端地址 ──
    # 若未设置则回退到 Mock 实现；设置了则创建 Remote* 实现
    search_service_url: str = os.getenv("SEARCH_SERVICE_URL", "")

    # ── Prompt 组装配置 ──
    prompt_default_max_tokens: int = int(os.getenv("PROMPT_DEFAULT_MAX_TOKENS", "4096"))
    prompt_dedup_similarity_threshold: float = float(os.getenv("PROMPT_DEDUP_THRESHOLD", "0.92"))
    prompt_default_role_hint: str = os.getenv("PROMPT_DEFAULT_ROLE_HINT", "资深软件工程师")

    @property
    def env(self) -> str:
        return os.getenv("MCP_ENV", "development")


server_config = ServerConfig()
