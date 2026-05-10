"""文档分类器 —— 根据目录、文件名、内容关键词推断文档类型（KnowledgeType）。

当前：规则匹配。
TODO: 后期接入 LLM 做分类。将文档前 N 行送入大模型，返回：
  1. 文档类型标签（rest_api_doc / sdk_api_doc / spec / best_practice / defect_history / general）
  2. （可选）API 章节的起止位置，替代 chunker.py 中的 _find_api_section_start + _REST_METHOD_PATTERN
替换 classify_document 的实现即可，调用方 orchestator 无需改动。
"""

from __future__ import annotations

import os
import re

from ..models.schemas import KnowledgeType

# 路径段关键词 → 类型
_DIR_PATTERNS: list[tuple[str, KnowledgeType]] = [
    ("spec", KnowledgeType.SPEC),
    ("best_practice", KnowledgeType.BEST_PRACTICE),
    ("practice", KnowledgeType.BEST_PRACTICE),
    ("defect", KnowledgeType.DEFECT_HISTORY),
    ("issue", KnowledgeType.DEFECT_HISTORY),
    ("bug", KnowledgeType.DEFECT_HISTORY),
    ("api", KnowledgeType.API),
    ("sdk", KnowledgeType.API),
]

# 文件名关键词 → 类型
_FILENAME_PATTERNS: list[tuple[str, KnowledgeType]] = [
    ("spec_", KnowledgeType.SPEC),
    ("_spec", KnowledgeType.SPEC),
    ("best_practice", KnowledgeType.BEST_PRACTICE),
    ("practice", KnowledgeType.BEST_PRACTICE),
    ("defect", KnowledgeType.DEFECT_HISTORY),
    ("_api_", KnowledgeType.API),
]

# 内容前 N 字符关键词 → 类型（按优先级排列）
_CONTENT_PATTERNS: list[tuple[re.Pattern, KnowledgeType]] = [
    (re.compile(r"(?:正例|反例|必须遵守|强制规范|Spec\s*契约|规范契约)"), KnowledgeType.SPEC),
    (re.compile(r"#{2,3}\s*(?:API|接口|Endpoints?)", re.IGNORECASE), KnowledgeType.API),
    (re.compile(r"###\s*(?:GET|POST|PUT|DELETE|PATCH)\s+/"), KnowledgeType.API),
    (re.compile(r"(?:最佳实践|Best\s*Practice|最佳做法)"), KnowledgeType.BEST_PRACTICE),
    (re.compile(r"(?:缺陷|Bug|根因|修复方案|故障复盘)"), KnowledgeType.DEFECT_HISTORY),
]

# 文件扩展名 → 类型
_EXT_PATTERNS: dict[str, KnowledgeType] = {
    ".java": KnowledgeType.API,
    ".py": KnowledgeType.API,
    ".go": KnowledgeType.API,
}


def classify_document(
    filepath: str,
    content_preview: str = "",
) -> KnowledgeType:
    """推断文档/文件的 KnowledgeType。

    优先级: 扩展名 > 目录路径（用户主动组织）> 文件名 > 内容关键词（兜底）> 默认 document

    目录结构是用户显式的分类意图——放在 spec/ 下就是 spec。内容关键词仅用于
    目录/文件名都无法判断时的自动推断。
    """
    # 0. 扩展名（代码文件直接判定）
    ext = os.path.splitext(filepath)[1].lower()
    if ext in _EXT_PATTERNS:
        return _EXT_PATTERNS[ext]

    filename = os.path.basename(filepath).lower()
    dir_path = os.path.dirname(filepath).replace("\\", "/").lower()

    # 1. 目录路径（用户主动分类，最可靠）
    for keyword, ktype in _DIR_PATTERNS:
        if keyword in dir_path.split("/"):
            return ktype

    # 2. 文件名关键词
    for keyword, ktype in _FILENAME_PATTERNS:
        if keyword in filename:
            return ktype

    # 3. 内容关键词（兜底：目录/文件名都看不出意图时自动推断）
    if content_preview:
        for pattern, ktype in _CONTENT_PATTERNS:
            if pattern.search(content_preview):
                return ktype

    # 4. 默认
    return KnowledgeType.DOCUMENT
