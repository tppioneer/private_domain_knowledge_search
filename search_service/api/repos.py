"""GET /api/v1/repos —— 列出已导入的仓库。"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter

from ..config import service_config

router = APIRouter()


@router.get("/repos")
async def list_repos() -> dict:
    """返回所有已索引仓库的列表和统计信息。"""
    reg_path = os.path.join(service_config.repo_data_dir, "registry.json")
    if not os.path.exists(reg_path):
        return {"repos": [], "total": 0}

    try:
        with open(reg_path, encoding="utf-8") as f:
            registry = json.load(f)
    except Exception:
        return {"repos": [], "total": 0}

    repos_raw = registry.get("repos", {})
    repos = []
    for name, info in repos_raw.items():
        # 推断主语言
        java = info.get("java_chunks", 0)
        py = info.get("python_chunks", 0)
        ts = info.get("typescript_chunks", 0)
        max_lang = max(("java", java), ("python", py), ("typescript", ts), key=lambda x: x[1])
        language = max_lang[0] if max_lang[1] > 0 else "unknown"

        repos.append({
            "name": name,
            "path": info.get("path", ""),
            "language": language,
            "chunk_count": info.get("chunk_count", 0),
            "last_indexed": info.get("last_indexed", ""),
        })

    return {"repos": repos, "total": len(repos)}
