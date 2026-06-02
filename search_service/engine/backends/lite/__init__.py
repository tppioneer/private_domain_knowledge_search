"""轻量后端实现 —— SQLite FTS5 / FAISS / NetworkX。"""

import re

# FTS5 查询语法特殊字符 —— 这些字符在 FTS5 MATCH 中有操作符语义，必须去除，
# 否则 SQLite 会抛出语法错误。
_FTS5_SYNTAX_RE = re.compile(r"""["*^()~!<>@#$%{}\[\]|:\-,]""")

# FTS5 布尔操作符（仅全大写时有特殊含义，小写化即可作为普通搜索词）
_FTS5_BOOLEAN_OPS = frozenset({"AND", "OR", "NOT"})


def sanitize_fts5_query(query: str) -> str:
    """将用户输入清理为安全的 FTS5 查询字符串。

    - 语法字符（引号、括号、星号等）→ 去除，防止 SQL 报错
    - . 替换为空格，展开全限定类名
    - 全限定类名（无空格且含 .）→ AND 拼接，避免 com 噪声
    - 自然语言（有空格）→ OR 拼接，宽召回
    """
    # 判断查询模式：纯代码引用还是自然语言
    is_code_ref = " " not in query and "." in query

    terms = query.split()
    safe_terms: list[str] = []

    for term in terms:
        cleaned = _FTS5_SYNTAX_RE.sub(" ", term)
        cleaned = cleaned.replace(".", " ")
        for sub in cleaned.split():
            if not sub or sub.isdigit():
                continue
            if sub.upper() in _FTS5_BOOLEAN_OPS:
                sub = sub.lower()
            safe_terms.append(sub)

    if not safe_terms:
        return '""'

    joiner = " AND " if is_code_ref else " OR "
    return joiner.join(safe_terms)
