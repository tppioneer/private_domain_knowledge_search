"""Prompt注入防护 —— 清洗检索文本中的越狱/注入短语。

安全策略：
1. 零宽字符过滤
2. Unicode规范化（消除同形异义攻击）
3. 越狱模式检测（支持Base64/URL编码）
4. HTML/代码注入防护
5. meta字段全量清洗
6. 安全审计日志记录（多平台兼容）

日志规范：
- 所有安全事件记录到专用日志器 "mcp.prompt.security"
- 使用标准logging级别：WARNING（越狱模式）、INFO（清洗操作）
- 日志字段遵循结构化日志规范，便于SIEM系统收集
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import unicodedata
import uuid
from urllib.parse import unquote

_security_logger = logging.getLogger("mcp.prompt.security")

_ZERO_WIDTH_CHARS = re.compile(
    r'[\u200b\u200c\u200d\u200e\u200f'
    r'\ufeff\u00ad\u180e'
    r'\ufff9-\ufffb'
    r'\u2060-\u2064]'
)

_BASE64_INJECTION_PATTERN = re.compile(
    r'(?:base64|c2Fwc2VjcmV0|bj9zaGlmdA==|ignore)'
    r'[a-zA-Z0-9+/=]{10,}',
    re.IGNORECASE
)

_JAILBREAK_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'忽略\s*[\u200b]?\s*(?:之前的|以上|一切|所有)\s*[\u200b]?\s*限制'), "removed"),
    (re.compile(r'ignore\s*[\u200b]?\s*(?:previous|all|above)\s*[\u200b]?\s*instructions', re.IGNORECASE), "removed"),
    (re.compile(r'dan\s*模式|developer\s*mode|越狱|jailbreak', re.IGNORECASE), "cleaned"),
    (re.compile(r'<script[^>]*>.*?</script>', re.IGNORECASE | re.DOTALL), "removed"),
    (re.compile(r'{{.*?}}'), "removed"),
    (re.compile(r'eval\s*\(|exec\s*\('), "removed"),
    (re.compile(r'alert\s*\(|prompt\s*\(|confirm\s*\('), "removed"),
    (re.compile(r'eval\s*atob\s*\(|fromCharCode\s*\('), "removed"),
]

_META_SANITIZE_FIELDS = [
    "code_example",
    "related_entity",
    "security_rule",
    "config_required",
    "definition_text",
    "definition",
    "source",
]


def _log_security_event(
    event_type: str,
    severity: str,
    item_id: str,
    matched_pattern: str | None = None,
    action: str | None = None,
    session_id: str | None = None,
) -> str:
    audit_id = f"sec_{uuid.uuid4().hex[:12]}"
    log_data = {
        "audit_id": audit_id,
        "event_type": event_type,
        "severity": severity,
        "item_id": item_id,
        "action_taken": action or "cleaned",
    }
    if matched_pattern:
        log_data["matched_pattern"] = matched_pattern
    if session_id:
        log_data["session_id"] = session_id

    if severity == "HIGH":
        _security_logger.warning(
            "Security event: %s | %s",
            event_type,
            " | ".join(f"{k}={v}" for k, v in log_data.items())
        )
    else:
        _security_logger.info(
            "Sanitization: %s | %s",
            event_type,
            " | ".join(f"{k}={v}" for k, v in log_data.items())
        )

    return audit_id


def _normalize_unicode(text: str) -> tuple[str, bool]:
    normalized = unicodedata.normalize("NFKC", text)
    return normalized, normalized != text


def _remove_zero_width(text: str) -> tuple[str, bool]:
    cleaned = _ZERO_WIDTH_CHARS.sub('', text)
    return cleaned, cleaned != text


def _decode_and_check(text: str) -> tuple[str, bool]:
    decoded_any = False
    original = text

    if _BASE64_INJECTION_PATTERN.search(text):
        try:
            decoded = base64.b64decode(text).decode('utf-8', errors='ignore')
            for pattern, action in _JAILBREAK_PATTERNS:
                if pattern.search(decoded):
                    _log_security_event(
                        event_type="base64_encoded_injection",
                        severity="HIGH",
                        item_id="decoded_content",
                        matched_pattern=pattern.pattern,
                        action=action,
                    )
                    decoded_any = True
                    break
        except Exception:
            pass

    try:
        url_decoded = unquote(text)
        if url_decoded != text:
            for pattern, action in _JAILBREAK_PATTERNS:
                if pattern.search(url_decoded):
                    _log_security_event(
                        event_type="url_encoded_injection",
                        severity="HIGH",
                        item_id="url_decoded_content",
                        matched_pattern=pattern.pattern,
                        action=action,
                    )
                    decoded_any = True
                    break
    except Exception:
        pass

    return original, decoded_any


def _sanitize_text(text: str) -> tuple[str, bool]:
    if not text:
        return text, False

    modified = False
    original = text

    text, changed = _remove_zero_width(text)
    modified = modified or changed

    text, changed = _normalize_unicode(text)
    modified = modified or changed

    original = text
    for pattern, action in _JAILBREAK_PATTERNS:
        new_text = pattern.sub('[已移除]', text)
        if new_text != text:
            _log_security_event(
                event_type="jailbreak_pattern_detected",
                severity="HIGH",
                item_id="content",
                matched_pattern=pattern.pattern,
                action=action,
            )
            text = new_text
            modified = True

    return text, modified


def sanitize_content(text: str) -> tuple[str, bool]:
    """对单条文本执行清洗，返回 (清洗后文本, 是否被修改)。"""
    return _sanitize_text(text)


def sanitize_items(
    items: list[dict],
    session_id: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """批量清洗知识片段及meta字段。

    Args:
        items: 知识片段列表
        session_id: 可选的会话ID，用于日志关联

    Returns:
        (cleaned_items, sanitization_log)
    """
    cleaned: list[dict] = []
    log: list[dict] = []

    for item in items:
        cleaned_item = dict(item)
        any_modified = False
        item_id = item.get("id", "unknown")

        content = item.get("content", "")
        cleaned_content, content_modified = sanitize_content(content)
        cleaned_item["content"] = cleaned_content
        any_modified = any_modified or content_modified

        meta = dict(item.get("meta", {}))
        meta_modified = False
        for field in _META_SANITIZE_FIELDS:
            if field in meta and meta[field]:
                if isinstance(meta[field], str):
                    sanitized, changed = sanitize_content(meta[field])
                    if changed:
                        meta[field] = sanitized
                        meta_modified = True
                        _log_security_event(
                            event_type="meta_field_sanitized",
                            severity="MEDIUM",
                            item_id=item_id,
                            matched_pattern=f"meta.{field}",
                            action="cleaned",
                            session_id=session_id,
                        )
                elif isinstance(meta[field], list):
                    new_list = []
                    for entry in meta[field]:
                        if isinstance(entry, str):
                            sanitized, changed = sanitize_content(entry)
                            if changed:
                                meta_modified = True
                                _log_security_event(
                                    event_type="meta_field_sanitized",
                                    severity="MEDIUM",
                                    item_id=item_id,
                                    matched_pattern=f"meta.{field}[list]",
                                    action="cleaned",
                                    session_id=session_id,
                                )
                            new_list.append(sanitized)
                        else:
                            new_list.append(entry)
                    meta[field] = new_list

        if meta_modified:
            any_modified = True

        cleaned_item["meta"] = meta

        if any_modified:
            audit_id = _log_security_event(
                event_type="knowledge_item_sanitized",
                severity="HIGH",
                item_id=item_id,
                action="cleaned",
                session_id=session_id,
            )
            log.append({
                "item_id": item_id,
                "original_hash": hashlib.md5(str(item).encode()).hexdigest()[:8],
                "action": "cleaned",
                "audit_id": audit_id,
            })

        cleaned.append(cleaned_item)

    return cleaned, log
