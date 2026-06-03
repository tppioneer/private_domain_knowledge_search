"""文件加载器 —— 从目录中加载 Markdown 和代码文件。"""

from __future__ import annotations

from pathlib import Path

from .models import FileRecord

_DEFAULT_EXCLUDE_DIRS = {".venv", "node_modules", "target", "build", ".git", "__pycache__", ".idea"}


def load_files(
    root_dir: str,
    patterns: list[str] | None = None,
    exclude_dirs: set | None = None,
    include_extensions: list[str] | None = None,
    exclude_extensions: list[str] | None = None,
) -> list[FileRecord]:
    if exclude_dirs is None:
        exclude_dirs = _DEFAULT_EXCLUDE_DIRS
    if patterns is None:
        patterns = ["**/*.md", "**/*.py", "**/*.go", "**/*.json", "**/*.yaml", "**/*.yml"]

    files: list[FileRecord] = []
    root = Path(root_dir)
    for pattern in patterns:
        for filepath in root.glob(pattern):
            if _path_in_dirs(filepath, root, exclude_dirs):
                continue
            if include_extensions and filepath.suffix.lower() not in include_extensions:
                continue
            if not include_extensions and exclude_extensions and filepath.suffix.lower() in exclude_extensions:
                continue
            try:
                content = filepath.read_text(encoding="utf-8")
            except Exception:
                continue
            files.append(FileRecord(
                path=str(filepath.relative_to(root)),
                filename=filepath.name,
                content=content,
            ))
    return files


def _path_in_dirs(filepath: Path, root: Path, exclude_dirs: set) -> bool:
    for part in filepath.relative_to(root).parts[:-1]:
        if part in exclude_dirs:
            return True
    return False
