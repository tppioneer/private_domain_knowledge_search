"""文档分类器 —— 根据目录、文件名、内容关键词推断文档类型（字符串）。"""

from __future__ import annotations

import os
import re

API = "api"
DOCUMENT = "document"
BEST_PRACTICE = "best_practice"
DEFECT_HISTORY = "defect_history"
SPEC = "spec"

_DIR_PATTERNS: list[tuple[str, str]] = [
    ("spec", SPEC), ("best_practice", BEST_PRACTICE), ("practice", BEST_PRACTICE),
    ("defect", DEFECT_HISTORY), ("issue", DEFECT_HISTORY), ("bug", DEFECT_HISTORY),
    ("api", API), ("sdk", API),
]
_FILENAME_PATTERNS: list[tuple[str, str]] = [
    ("spec_", SPEC), ("_spec", SPEC), ("best_practice", BEST_PRACTICE),
    ("practice", BEST_PRACTICE), ("defect", DEFECT_HISTORY), ("_api_", API),
]
_CONTENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:正例|反例|必须遵守|强制规范|Spec\s*契约|规范契约)"), SPEC),
    (re.compile(r"#{2,3}\s*(?:API|接口|Endpoints?)", re.IGNORECASE), API),
    (re.compile(r"###\s*(?:GET|POST|PUT|DELETE|PATCH)\s+/"), API),
    (re.compile(r"(?:最佳实践|Best\s*Practice|最佳做法)"), BEST_PRACTICE),
    (re.compile(r"(?:缺陷|Bug|根因|修复方案|故障复盘)"), DEFECT_HISTORY),
]
_EXT_PATTERNS: dict[str, str] = {".java": API, ".py": API, ".go": API}


def classify_document(filepath: str, content_preview: str = "") -> str:
    """推断文档/文件的类型字符串。"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in _EXT_PATTERNS:
        return _EXT_PATTERNS[ext]
    filename = os.path.basename(filepath).lower()
    dir_path = os.path.dirname(filepath).replace("\\", "/").lower()
    for keyword, ktype in _DIR_PATTERNS:
        if keyword in dir_path.split("/"):
            return ktype
    for keyword, ktype in _FILENAME_PATTERNS:
        if keyword in filename:
            return ktype
    if content_preview:
        for pattern, ktype in _CONTENT_PATTERNS:
            if pattern.search(content_preview):
                return ktype
    return DOCUMENT
