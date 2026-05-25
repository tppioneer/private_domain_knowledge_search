"""Diff 分析器 —— 对比预期代码与实际生成代码，分类错误并定位根因。

输入: expected.java + actual.java + session metrics JSONL record
输出: ErrorReport 含分类错误 + 根因推断（recall_failure / ranking_failure / ...）
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


# ─── Java 代码提取正则 ───

_IMPORT_PATTERN = re.compile(r"^import\s+([\w.]+(?:\.\w+)*)\s*;", re.MULTILINE)
_METHOD_CALL_PATTERN = re.compile(r"(\w+(?:\.\w+)*)\s*\((.*?)\)", re.DOTALL)
_CLASS_NAME_PATTERN = re.compile(r"\bnew\s+(\w+)\s*\(")

# java.lang 类型，不需要 import
_JAVA_LANG = {
    "String", "Object", "Integer", "Long", "Float", "Double",
    "Boolean", "Byte", "Short", "Character", "Number", "Class",
    "void", "int", "long", "float", "double", "boolean", "byte", "short", "char",
}


@dataclass
class ImportError:
    expected_fqn: str = ""
    actual_fqn: str = ""
    simple_name: str = ""
    root_cause: str = ""  # recall_failure | ranking_failure | format_failure | unknown


@dataclass
class APIError:
    expected_api: str = ""
    actual_api: str = ""
    root_cause: str = ""


@dataclass
class ErrorReport:
    session_id: str = ""
    tool_calls: int = 0
    wrong_imports: list[ImportError] = field(default_factory=list)
    missing_imports: list[ImportError] = field(default_factory=list)
    wrong_methods: list[APIError] = field(default_factory=list)
    wrong_params: list[APIError] = field(default_factory=list)
    hallucinated_apis: list[APIError] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _extract_imports(code: str) -> dict[str, str]:
    """从 Java 代码提取 import 语句 → {simple_name: full_fqn}。"""
    imports: dict[str, str] = {}
    for m in _IMPORT_PATTERN.finditer(code):
        fqn = m.group(1)
        simple = fqn.rsplit(".", 1)[-1]
        imports[simple] = fqn
    return imports


def _extract_method_calls(code: str) -> list[dict]:
    """从代码中提取方法调用 → [{class_name?, method_name, params}]。"""
    calls: list[dict] = []
    for m in _METHOD_CALL_PATTERN.finditer(code):
        full_call = m.group(1)
        args = m.group(2).strip()
        parts = full_call.split(".")
        if len(parts) >= 2:
            calls.append({"class_hint": parts[-2], "method": parts[-1], "params": args})
        else:
            calls.append({"class_hint": "", "method": parts[0], "params": args})
    return calls


def _find_search_results_for_name(name: str, search_spans: list[dict]) -> tuple[bool, int]:
    """在 retrieval spans 中查找包含指定类名/方法名的条目。

    Returns:
        (found, best_rank) — found 表示是否被检索到，best_rank 是最佳排名（1-based）
    """
    best_rank = 999
    found = False
    for span in search_spans:
        items = span.get("meta", {}).get("items", [])
        for i, item in enumerate(items):
            content = item.get("content", "") if isinstance(item, dict) else ""
            meta = item.get("meta", {}) if isinstance(item, dict) else {}
            fqn = meta.get("class_name", "") if isinstance(meta, dict) else ""

            if name.lower() in content.lower() or name.lower() in fqn.lower():
                found = True
                if i + 1 < best_rank:
                    best_rank = i + 1
    return found, best_rank


def analyze(
    expected_code: str,
    actual_code: str,
    session_metrics: dict | None = None,
) -> ErrorReport:
    """分析预期与实际代码差异，生成错误报告。

    Args:
        expected_code: 预期生成的 Java 代码
        actual_code: 实际生成的 Java 代码
        session_metrics: 可选的 metrics JSONL record（含 spans/input/output）

    Returns:
        ErrorReport 含分类错误
    """
    report = ErrorReport()
    if session_metrics:
        report.session_id = session_metrics.get("session_id", "")
        report.tool_calls = session_metrics.get("tool_call_count", 1)

    exp_imports = _extract_imports(expected_code)
    act_imports = _extract_imports(actual_code)

    search_spans: list[dict] = []
    if session_metrics:
        for span in session_metrics.get("spans", []):
            if span.get("name") in ("remote_search", "get_entity"):
                search_spans.append(span)

    # ── Import 差异分析 ──
    for simple, exp_fqn in exp_imports.items():
        if simple in _JAVA_LANG:
            continue
        act_fqn = act_imports.get(simple)
        if act_fqn is None:
            # 缺少 import
            found, rank = _find_search_results_for_name(exp_fqn, search_spans)
            cause = _classify_cause(found, rank, exp_fqn, "")
            report.missing_imports.append(ImportError(
                expected_fqn=exp_fqn, simple_name=simple, root_cause=cause,
            ))
        elif act_fqn != exp_fqn:
            # 错误的 import
            found, rank = _find_search_results_for_name(exp_fqn, search_spans)
            cause = _classify_cause(found, rank, exp_fqn, act_fqn)
            report.wrong_imports.append(ImportError(
                expected_fqn=exp_fqn, actual_fqn=act_fqn, simple_name=simple, root_cause=cause,
            ))

    # ── 方法调用差异 ──
    exp_calls = _extract_method_calls(expected_code)
    act_calls = _extract_method_calls(actual_code)
    exp_methods = {(c["class_hint"], c["method"]) for c in exp_calls if c["class_hint"]}
    act_methods = {(c["class_hint"], c["method"]) for c in act_calls if c["class_hint"]}

    for class_hint, method in exp_methods - act_methods:
        full_name = f"{class_hint}.{method}"
        found, rank = _find_search_results_for_name(full_name, search_spans)
        cause = _classify_cause(found, rank, full_name, "")
        if found and rank <= 5:
            report.wrong_params.append(APIError(expected_api=full_name, root_cause=cause))
        else:
            report.wrong_methods.append(APIError(expected_api=full_name, root_cause=cause))

    # ── 幻觉 API 检测 ──
    for class_hint, method in act_methods - exp_methods:
        full_name = f"{class_hint}.{method}"
        # 只标记那些通过检索没返回的（可能是幻觉）
        in_act_imports = any(
            class_hint.lower() == s.lower() for s in act_imports
        )
        if not in_act_imports and class_hint not in _JAVA_LANG:
            report.hallucinated_apis.append(APIError(
                actual_api=full_name, root_cause="not_in_expected",
            ))

    # ── 汇总 ──
    total = (
        len(report.wrong_imports) + len(report.missing_imports)
        + len(report.wrong_methods) + len(report.wrong_params)
        + len(report.hallucinated_apis)
    )
    causes: dict[str, int] = {}
    for errs in (
        report.wrong_imports, report.missing_imports, report.wrong_methods,
        report.wrong_params, report.hallucinated_apis,
    ):
        for e in errs:
            causes[e.root_cause] = causes.get(e.root_cause, 0) + 1
    report.summary = {"total_errors": total, "by_cause": causes}
    return report


def _classify_cause(found: bool, rank: int, expected: str, actual: str) -> str:
    """根据检索结果推断错误根因。

    - recall_failure: 正确答案根本没被检索到
    - ranking_failure: 正确答案在结果中但排名太低（>5）
    - format_failure: 答案在 top-5 但 import 路径缺失（我们刚修复的）
    - unknown: 无法推断
    """
    if not found:
        return "recall_failure"
    if rank > 5:
        return "ranking_failure"
    if rank <= 5:
        return "format_failure"
    return "unknown"
