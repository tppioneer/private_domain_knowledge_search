"""MCP 工具调用埋点 —— 零依赖 JSON lines 结构化日志。

通过 MetricsContext context manager 记录每次 tool 调用的:
- trace_id / session_id / tool_name / 总耗时
- 子操作 span (remote_search, assemble, get_entity 等)
- 输入输出摘要

写入格式: 每行一条 JSON，路径 {log_dir}/YYYY-MM-DD.jsonl。
关闭: METRICS_ENABLED=false 时所有操作零开销 no-op。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Span:
    name: str
    duration_ms: float
    meta: dict = field(default_factory=dict)


@dataclass
class ToolCallRecord:
    trace_id: str
    session_id: str
    tool_name: str
    timestamp: str
    duration_ms: float
    spans: list[Span] = field(default_factory=list)
    input_summary: dict = field(default_factory=dict)
    output_summary: dict = field(default_factory=dict)
    error: str | None = None


class _SpanContext:
    """子操作计时 context manager。"""

    def __init__(self, name: str, parent: MetricsContext):
        self.name = name
        self.parent = parent
        self.meta: dict = {}

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *args):
        elapsed = (time.perf_counter() - self._t0) * 1000
        self.parent._spans.append(Span(
            name=self.name,
            duration_ms=round(elapsed, 3),
            meta=self.meta,
        ))


class MetricsContext:
    """Tool 级埋点 context manager。

    with MetricsContext("search_private_knowledge", session_id="sess_001") as mc:
        with mc.span("remote_search") as span:
            result = ...
            span.meta["item_count"] = len(result.items)
        mc.set_output(result_count=8, has_entry_point=True)
    """

    def __init__(
        self,
        tool_name: str,
        session_id: str = "",
        log_dir: str | None = None,
        enabled: bool = True,
    ):
        self._enabled = enabled
        if not enabled:
            return
        self._tool_name = tool_name
        self._session_id = session_id
        self._trace_id = _short_id()
        self._t0: float = 0.0
        self._spans: list[Span] = []
        self._output: dict = {}
        self._error: str | None = None
        self._input: dict = {}
        self._log_dir = log_dir or os.path.join(os.getcwd(), "logs", "metrics")

    # ── 输入摘要 ──

    def set_input(self, **kwargs) -> None:
        if self._enabled:
            self._input.update(kwargs)

    # ── 输出摘要 ──

    def set_output(self, **kwargs) -> None:
        if self._enabled:
            self._output.update(kwargs)

    # ── 异常记录 ──

    def set_error(self, error_msg: str) -> None:
        if self._enabled:
            self._error = error_msg

    # ── 子操作 span ──

    def span(self, name: str) -> _SpanContext:
        if not self._enabled:
            return _SpanContext(name, self)  # no-op, timing is just ignored on exit
        return _SpanContext(name, self)

    # ── enter / exit ──

    def __enter__(self):
        if self._enabled:
            self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._enabled:
            return False
        duration = (time.perf_counter() - self._t0) * 1000
        if exc_type is not None and self._error is None:
            self._error = f"{exc_type.__name__}: {exc_val}"
        record = ToolCallRecord(
            trace_id=self._trace_id,
            session_id=self._session_id,
            tool_name=self._tool_name,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            duration_ms=round(duration, 3),
            spans=self._spans,
            input_summary=self._input,
            output_summary=self._output,
            error=self._error,
        )
        self._write(record)
        return False  # 不抑制异常

    def _write(self, record: ToolCallRecord) -> None:
        try:
            log_path = Path(self._log_dir)
            log_path.mkdir(parents=True, exist_ok=True)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            filepath = log_path / f"{today}.jsonl"
            line = json.dumps(_sanitize_record(asdict(record)), ensure_ascii=False)
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            logger.warning("failed to write metrics record", exc_info=True)


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


def _sanitize_record(d: dict) -> dict:
    """将 span/dict 中的非 JSON 兼容值转为字符串（datetime 等）。"""
    if isinstance(d, dict):
        return {k: _sanitize_record(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_sanitize_record(v) for v in d]
    if isinstance(d, (datetime,)):
        return d.isoformat()
    if hasattr(d, "__dict__"):
        return str(d)
    return d
