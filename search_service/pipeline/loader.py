"""文件加载器 —— 从目录中加载 Markdown 和代码文件。"""

from __future__ import annotations

import os
from pathlib import Path


def load_files(
    root_dir: str,
    patterns: list[str] | None = None,
) -> list[dict]:
    """加载指定模式的文件，返回 [{path, filename, content}]。

    Args:
        root_dir: 根目录
        patterns: glob 模式列表，默认 ["**/*.md", "**/*.py", "**/*.go"]
    """
    if patterns is None:
        patterns = ["**/*.md", "**/*.py", "**/*.go"]

    files: list[dict] = []
    root = Path(root_dir)

    for pattern in patterns:
        for filepath in root.glob(pattern):
            try:
                content = filepath.read_text(encoding="utf-8")
            except Exception:
                continue
            files.append({
                "path": str(filepath.relative_to(root)),
                "filename": filepath.name,
                "content": content,
            })

    return files
