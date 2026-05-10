"""文档分块器 —— 语义边界识别 + 重叠窗口策略 + API 章节特殊处理。

通用文档：按空行切分段落，合并至 3K-5K tokens，块间重叠窗口。
MD API 文档：识别 ## API 章节，内部按 ### 子标题切分，每个 API = 一个完整 chunk。
"""

from __future__ import annotations

import hashlib
import json
import re

# 中文约 1.5 char/token，英文约 2.5 char/token，按综合 2.0 估算
_CHARS_PER_TOKEN = 2.0
_MIN_CHUNK_SIZE = 3000  # ~1500 tokens
_MAX_CHUNK_SIZE = 10000  # ~5000 tokens
_OVERLAP_SIZE = 600  # ~300 tokens

# API 章节标题模式（中英文）
_API_SECTION_PATTERN = re.compile(
    r"^#{2,3}\s*(?:API\s*(?:接口|文档|列表|参考|说明)?|"
    r"(?:接口|API)\s*(?:定义|文档|列表|说明|参考)?|"
    r"Endpoints?|"
    r"REST\s*API|"
    r"对外接口|"
    r"接口文档)",
    re.IGNORECASE | re.MULTILINE,
)

# REST 方法 + 路径模式，用于提取 HTTP 元数据
_REST_METHOD_PATTERN = re.compile(
    r"(?:###?\s*)?(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/\S+)",
    re.IGNORECASE,
)


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def _split_paragraphs(text: str) -> list[str]:
    """按空行 + 标题边界切分段落。"""
    parts = re.split(r"\n\s*\n", text)
    merged: list[str] = []
    buf = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        buf = (buf + "\n\n" + p).strip() if buf else p
        if len(buf) >= _MIN_CHUNK_SIZE:
            merged.append(buf)
            buf = ""
    if buf:
        merged.append(buf)
    return merged


def _find_api_section_start(text: str) -> int | None:
    """定位 API 章节起始位置。返回 None 表示没有 API 章节。"""
    # 优先匹配 ## API 或 ### API 级别的标题
    for pattern in [
        r"^##\s*(?:API|接口|Endpoints?|对外接口|REST\s*API)",
        r"^###\s*(?:API|接口|Endpoints?|对外接口|REST\s*API)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.start()
    return None


def _split_api_entries(api_text: str) -> list[dict]:
    """将 API 章节内容按 ###（或 ####）子标题切分为独立条目。

    Returns:
        [{title, content, http_method?, url_path?}]
    """
    # 按 ### 或 #### 子标题切分
    parts = re.split(r"\n(?=###(?!#)|####(?!#))", api_text)
    entries: list[dict] = []

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # 提取子标题作为 API 名称
        title_match = re.match(r"(?:#{2,4})\s*(.+)", part)
        api_title = title_match.group(1).strip() if title_match else ""
        content = part

        # 过滤纯章节标题（没有实质内容的标题行）
        body_text = part[title_match.end():].strip() if title_match else part
        if not body_text:
            continue

        entry: dict = {"title": api_title, "content": content}

        # 检测 REST 模式
        rest_match = _REST_METHOD_PATTERN.search(content)
        if rest_match:
            entry["http_method"] = rest_match.group(1).upper()
            entry["url_path"] = rest_match.group(2)

        entries.append(entry)

    return entries


def _build_chunk_dict(
    content: str,
    source_path: str,
    title: str,
    chunk_type: str,
    module: str,
    start_idx: int = 0,
    extra_meta: dict | None = None,
) -> dict:
    """构造统一的 chunk 字典。"""
    meta = {
        "knowledge_source": "doc",
        "source": source_path,
        "type": chunk_type,
    }
    if extra_meta:
        meta.update(extra_meta)

    return {
        "id": _make_chunk_id(source_path, start_idx),
        "type": chunk_type,
        "content": content,
        "title": title,
        "module": module,
        "source_path": source_path,
        "tokens": _estimate_tokens(content),
        "meta_json": json.dumps(meta, ensure_ascii=False),
    }


def chunk_text(
    text: str,
    source_path: str = "",
    title: str = "",
    chunk_type: str = "document",
    module: str = "",
) -> list[dict]:
    """将文本切分为 chunk 列表。

    - 包含 API 章节的 MD 文档：API 前内容按段落分块，API 内部按 ### 子标题分块
    - 普通文档/代码：按段落分块 + 重叠窗口
    """
    chunks: list[dict] = []
    start_idx = 0

    api_start = _find_api_section_start(text)

    # ── API 前内容：常规段落分块 ──
    if api_start is not None and api_start > 0:
        pre_text = text[:api_start].strip()
        if pre_text:
            for para in _split_paragraphs(pre_text):
                chunks.append(_build_chunk_dict(
                    para, source_path, title, "document", module, start_idx,
                ))
                start_idx += 1

    # ── API 章节：按子标题切分 ──
    if api_start is not None:
        api_text = text[api_start:]
        entries = _split_api_entries(api_text)
        for entry in entries:
            extra: dict = {}
            if entry.get("http_method"):
                extra["api_type"] = "rest"
                extra["http_method"] = entry["http_method"]
                extra["url_path"] = entry.get("url_path", "")
            chunks.append(_build_chunk_dict(
                entry["content"],
                source_path,
                entry["title"] or title,
                "api",
                module,
                start_idx,
                extra,
            ))
            start_idx += 1
        return chunks

    # ── 无 API 章节：常规段落分块 ──
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    current = ""
    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        if not current:
            current = para
        else:
            combined = current + "\n\n" + para
            if len(combined) > _MAX_CHUNK_SIZE:
                chunks.append(_build_chunk_dict(
                    current, source_path, title, chunk_type, module, start_idx,
                ))
                overlap_text = current[-_OVERLAP_SIZE:] if len(current) > _OVERLAP_SIZE else ""
                current = overlap_text + "\n\n" + para
                start_idx = i
            else:
                current = combined
        i += 1

    if current:
        chunks.append(_build_chunk_dict(
            current, source_path, title, chunk_type, module, start_idx,
        ))

    return chunks


def _make_chunk_id(source_path: str, index: int) -> str:
    raw = f"{source_path}:{index}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]
