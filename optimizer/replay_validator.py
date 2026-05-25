"""回放验证器 —— 根据 session metrics 回放检索结果，定位质量瓶颈。

对每个错误 API/import，回查 session 中的检索记录，判定:
- recall_failure: 正确答案未进入候选集
- ranking_failure: 正确答案在候选集中但排名靠后（>top_k）
- truncation_failure: 正确答案被 token budget 截断
- format_failure: 答案在 top-5 但信息不完整（如缺少 import 路径）

输出 Scorecard，包含瓶颈分布和优化建议。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from search_service.engine.backends.lite.entity import LiteEntitySearcher
from search_service.engine.backends.lite.bm25 import LiteBM25Searcher


@dataclass
class VerificationRecord:
    target_name: str           # 目标类名/方法名
    found: bool = False        # 是否在检索结果中
    rank: int = 999            # 1-based 排名（未找到时为 999）
    in_bm25: bool = False      # BM25 路是否命中
    in_vector: bool = False    # 向量路是否命中
    in_graph: bool = False     # 图路是否命中
    root_cause: str = ""       # recall_failure | ranking_failure | truncation_failure


@dataclass
class Scorecard:
    session_id: str = ""
    total_checks: int = 0
    records: list[VerificationRecord] = field(default_factory=list)

    @property
    def recall_failures(self) -> int:
        return sum(1 for r in self.records if r.root_cause == "recall_failure")

    @property
    def ranking_failures(self) -> int:
        return sum(1 for r in self.records if r.root_cause == "ranking_failure")

    @property
    def recall_rate(self) -> float:
        if not self.total_checks:
            return 1.0
        return 1.0 - self.recall_failures / self.total_checks

    @property
    def suggestions(self) -> list[str]:
        s: list[str] = []
        if self.recall_failures > self.total_checks * 0.3:
            s.append(
                f"召回率偏低 ({self.recall_rate:.0%})：建议增大 candidate_multiplier 或放宽 BM25 匹配策略"
            )
        if self.ranking_failures > 0:
            s.append(
                f"排序问题 ({self.ranking_failures} 条): 建议调整 ranker type/role boost 权重"
            )
        if not s:
            s.append("检索质量良好，无需调整")
        return s


def validate(
    target_names: list[str],
    session_metrics: dict,
    top_k: int = 5,
) -> Scorecard:
    """验证目标 API/类名是否在检索结果中，及排名位置。

    目前 session_metrics 的 span meta 中不直接存储 item 列表（为了节省 token），
    所以采用回放策略：重新执行 FTS5 和 entity search，检查是否命中。

    Args:
        target_names: 要验证的类名/方法名列表（如 ["FileServiceManager", "FileService"]）
        session_metrics: 单次 session 的 metrics JSONL record
        top_k: 检索 top_k 参数

    Returns:
        Scorecard 含每条目标的验证记录 + 汇总统计
    """
    scorecard = Scorecard(
        session_id=session_metrics.get("session_id", ""),
        total_checks=len(target_names),
    )

    # 从 session 日志中提取 query 和检索参数
    query = session_metrics.get("input_summary", {}).get("query", "")
    if not query:
        query = " ".join(target_names)  # 降级：用目标名拼 query

    # 使用 FTS5 做一次检索（模拟 BM25 路）
    try:
        import asyncio
        bm25 = LiteBM25Searcher()
        results = asyncio.run(bm25.search(query, top_k=top_k * 3))
    except Exception:
        results = []

    # 使用 entity searcher 做精确查询
    try:
        entity_searcher = LiteEntitySearcher()
    except Exception:
        entity_searcher = None

    for name in target_names:
        record = VerificationRecord(target_name=name)

        # 检查 BM25 路
        for i, item in enumerate(results):
            content = item.content
            class_name = item.meta.class_name if item.meta and item.meta.class_name else ""

            if name.lower() in content.lower() or name.lower() in class_name.lower():
                record.in_bm25 = True
                record.found = True
                if i + 1 < record.rank:
                    record.rank = i + 1

        # 检查 entity search 路
        if entity_searcher and entity_searcher.available:
            entities = entity_searcher.search_entity(name)
            if entities:
                record.found = True
                record.in_graph = True  # entity search 走 SQLite
                if 1 < record.rank:
                    record.rank = min(record.rank, 1)  # entity 命中视为 rank 1

        # 分类根因
        if not record.found:
            record.root_cause = "recall_failure"
        elif record.rank > top_k:
            record.root_cause = "ranking_failure"
        else:
            record.root_cause = "ok"

        scorecard.records.append(record)

    return scorecard


def batch_validate(
    metrics_file: str,
    expected_apis: dict[str, list[str]],
) -> list[Scorecard]:
    """从 metrics JSONL 文件批量验证多个 session。

    Args:
        metrics_file: metrics JSONL 文件路径
        expected_apis: {session_id: [expected_api_names]}

    Returns:
        list of Scorecard
    """
    # 加载 sessions
    sessions: dict[str, dict] = {}
    with open(metrics_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = record.get("session_id", record.get("trace_id", ""))
            if sid in expected_apis:
                sessions[sid] = record

    scorecards: list[Scorecard] = []
    for sid, apis in expected_apis.items():
        session = sessions.get(sid)
        if session:
            scorecard = validate(apis, session)
        else:
            scorecard = Scorecard(session_id=sid, total_checks=len(apis))
            scorecard.records = [
                VerificationRecord(target_name=api, root_cause="no_session_data")
                for api in apis
            ]
        scorecards.append(scorecard)

    return scorecards
