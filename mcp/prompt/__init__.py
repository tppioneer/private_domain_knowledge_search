"""Prompt 组装模块 —— 将检索结果加工为 LLM 可直接消费的结构化 Prompt。

流水线: 安全清洗 → 去重 → 分组排序 → Token裁剪 → 4段式组装
"""

from .assemble_prompt import assemble_prompt
from .sanitizer import sanitize_items
from .deduplicator import deduplicate_items
from .assembler import assemble_sections
