"""多语言 tree-sitter adapter 统一入口。"""

from __future__ import annotations

_LANG_MAP: dict[str, str] = {
    ".java": "java",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
}


def parse_source(language: str, source: str, tsx: bool = False):
    """按语言解析源码，返回 TreeAdapter。"""
    if language == "java":
        from . import java
        return java.parse(source)
    elif language == "python":
        from . import python
        return python.parse(source)
    elif language == "typescript":
        from . import typescript
        return typescript.parse(source, tsx=tsx)
    else:
        raise ValueError(f"unsupported language: {language}")


def extract_calls(language: str, method_wrapper):
    """按语言提取方法调用。"""
    if language == "java":
        from . import java
        return java.extract_calls(method_wrapper)
    elif language == "python":
        from . import python
        return python.extract_calls(method_wrapper)
    elif language == "typescript":
        from . import typescript
        return typescript.extract_calls(method_wrapper)
    return []


def detect_language(filepath: str) -> str:
    """从文件扩展名检测语言。"""
    import os
    ext = os.path.splitext(filepath)[1].lower()
    return _LANG_MAP.get(ext, "java")
