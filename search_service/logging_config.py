"""全局日志配置 —— 所有模块统一输出到 logs/ 目录。

日志文件:
  logs/search_service.log   — 所有 search_service 模块
  logs/mcp_server.log       — 所有 inner_sdk_search_mcp 模块
  logs/access.log           — HTTP 请求日志（uvicorn）

日志级别: LOG_LEVEL 环境变量（默认 INFO）
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = os.path.join(os.getcwd(), "logs")
_DEFAULT_FMT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_DEFAULT_DATE = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5


def _level() -> int:
    name = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, name, logging.INFO)


def _ensure_dir() -> str:
    os.makedirs(_LOG_DIR, exist_ok=True)
    return _LOG_DIR


def setup_service_logging(prefix: str = "search_service") -> None:
    """配置 Search Service 的全局日志。

    Args:
        prefix: 日志文件名前缀 ("search_service" | "mcp_server")
    """
    log_dir = _ensure_dir()
    level = _level()

    # 文件 handler（所有级别）
    file_path = os.path.join(log_dir, f"{prefix}.log")
    file_handler = RotatingFileHandler(
        file_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_DEFAULT_FMT, _DEFAULT_DATE))

    # 控制台 handler（WARNING 以上）
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter(_DEFAULT_FMT, _DEFAULT_DATE))

    # 根 logger
    root = logging.getLogger()
    root.setLevel(level)

    # 清除已有的 handlers，避免重复（uvicorn 可能已添加）
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # 第三方库保持安静
    for lib in ("httpx", "faiss", "urllib3", "asyncio"):
        logging.getLogger(lib).setLevel(logging.WARNING)
    # uvicorn.access 单独写 access.log
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers.clear()
    uvicorn_access.propagate = False  # 不输出到主日志
    access_handler = RotatingFileHandler(
        os.path.join(log_dir, "access.log"), maxBytes=_MAX_BYTES,
        backupCount=2, encoding="utf-8",
    )
    access_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", _DEFAULT_DATE))
    uvicorn_access.addHandler(access_handler)


def reset_hybrid_search_logger() -> None:
    """清理 hybrid_searcher 中的 ad-hoc logger，统一到全局日志。"""
    hlog = logging.getLogger("hybrid_searcher")
    hlog.handlers.clear()
    hlog.propagate = True  # 回到根 logger 的统一输出
