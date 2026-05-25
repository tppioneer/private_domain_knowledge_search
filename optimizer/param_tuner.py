"""参数优化器 —— 调优 ranker 权重参数（type boost / role boost / layer boost）。

默认用等距网格搜索（零额外依赖）。可安装 scikit-optimize 启用 Bayesian optimization:
    pip install scikit-optimize

使用方式:
    # 用测试用例评估当前默认权重
    python -m optimizer.param_tuner --evaluate test_cases.json

    # 网格搜索最优权重
    python -m optimizer.param_tuner --tune test_cases.json --output weights.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 可调参数定义 ──
# (name, default, min, max, step_count)
_PARAM_DEFS: list[dict] = [
    {"name": "type_boost_api", "default": 1.0, "min": 0.90, "max": 1.10, "steps": 5},
    {"name": "type_boost_best_practice", "default": 0.95, "min": 0.85, "max": 1.05, "steps": 5},
    {"name": "type_boost_defect_history", "default": 0.90, "min": 0.80, "max": 1.00, "steps": 5},
    {"name": "type_boost_security_rule", "default": 0.85, "min": 0.75, "max": 0.95, "steps": 5},
    {"name": "type_boost_term", "default": 0.80, "min": 0.70, "max": 0.90, "steps": 5},
    {"name": "type_boost_spec", "default": 0.95, "min": 0.85, "max": 1.05, "steps": 5},
    {"name": "type_boost_test_template", "default": 0.75, "min": 0.65, "max": 0.85, "steps": 5},
    {"name": "type_boost_document", "default": 0.90, "min": 0.80, "max": 1.00, "steps": 5},
    {"name": "role_boost_entry_point", "default": 1.0, "min": 0.95, "max": 1.10, "steps": 4},
    {"name": "role_boost_public_api", "default": 0.92, "min": 0.85, "max": 1.00, "steps": 4},
    {"name": "role_boost_internal", "default": 0.80, "min": 0.70, "max": 0.90, "steps": 4},
    {"name": "layer_boost_high", "default": 1.30, "min": 1.15, "max": 1.45, "steps": 4},
    {"name": "layer_boost_low", "default": 0.80, "min": 0.65, "max": 0.95, "steps": 4},
    {"name": "keyword_hit_bonus", "default": 0.02, "min": 0.005, "max": 0.05, "steps": 4},
]


@dataclass
class TestCase:
    """单个测试用例：期望 top-k 中包含特定 API IDs。"""
    query: str = ""
    expected_ids: list[str] = field(default_factory=list)
    top_k: int = 5


@dataclass
class TuneResult:
    params: dict = field(default_factory=dict)
    score: float = 0.0
    # 回测详情
    test_cases: int = 0
    avg_reciprocal_rank: float = 0.0
    perfect_recalls: int = 0  # 所有 expected_ids 都在 top-k 的 case 数


def _param_space() -> list[list[float]]:
    """生成每个参数的搜索范围（均匀采样）。"""
    spaces: list[list[float]] = []
    for p in _PARAM_DEFS:
        step = (p["max"] - p["min"]) / max(p["steps"] - 1, 1)
        vals = [p["min"] + step * i for i in range(p["steps"])]
        spaces.append(vals)
    return spaces


def _default_params() -> dict[str, float]:
    return {p["name"]: p["default"] for p in _PARAM_DEFS}


def _apply_weights(weights: dict) -> None:
    """将优化结果写入 ranker 模块的全局权重常量。"""
    from search_service.engine import ranker

    # type boost
    ranker._TYPE_BOOST["api"] = weights.get("type_boost_api", 1.0)
    ranker._TYPE_BOOST["best_practice"] = weights.get("type_boost_best_practice", 0.95)
    ranker._TYPE_BOOST["defect_history"] = weights.get("type_boost_defect_history", 0.90)
    ranker._TYPE_BOOST["security_rule"] = weights.get("type_boost_security_rule", 0.85)
    ranker._TYPE_BOOST["term"] = weights.get("type_boost_term", 0.80)
    ranker._TYPE_BOOST["spec"] = weights.get("type_boost_spec", 0.95)
    ranker._TYPE_BOOST["test_template"] = weights.get("type_boost_test_template", 0.75)
    ranker._TYPE_BOOST["document"] = weights.get("type_boost_document", 0.90)

    # role boost
    ranker._ROLE_BOOST["entry_point"] = weights.get("role_boost_entry_point", 1.0)
    ranker._ROLE_BOOST["public_api"] = weights.get("role_boost_public_api", 0.92)
    ranker._ROLE_BOOST["internal"] = weights.get("role_boost_internal", 0.80)

    # layer boost
    ranker._LAYER_BOOST["high"] = weights.get("layer_boost_high", 1.30)
    ranker._LAYER_BOOST["low"] = weights.get("layer_boost_low", 0.80)


def _search_and_score(
    test_cases: list[TestCase],
    weights: dict,
) -> TuneResult:
    """用一组权重对测试用例做检索 + 评分。

    评分方式: Mean Reciprocal Rank (MRR) — 期望条目排名越高越好。
    """
    import asyncio
    from search_service.engine.hybrid_searcher import HybridSearcher

    _apply_weights(weights)
    searcher = HybridSearcher()

    result = TuneResult(params=weights, test_cases=len(test_cases))
    total_rr = 0.0

    for tc in test_cases:
        items, diagnostics, _ = asyncio.run(
            searcher.search(query=tc.query, top_k=tc.top_k)
        )

        case_rr = 0.0
        found_all = True
        for expected_id in tc.expected_ids:
            for i, item in enumerate(items):
                if item.id == expected_id or expected_id.lower() in item.content.lower():
                    case_rr += 1.0 / (i + 1)
                    break
                # 也按 content keyword 匹配（ID is MD5 hash, not human-readable）
                if expected_id.lower() in item.content.lower():
                    case_rr += 1.0 / (i + 1)
                    break
            else:
                found_all = False

        if found_all:
            result.perfect_recalls += 1
        if tc.expected_ids:
            total_rr += case_rr / len(tc.expected_ids)

    result.avg_reciprocal_rank = total_rr / max(len(test_cases), 1)
    result.score = result.avg_reciprocal_rank
    return result


def evaluate(test_cases: list[TestCase]) -> TuneResult:
    """用默认权重评估当前检索质量。"""
    return _search_and_score(test_cases, _default_params())


def grid_search(test_cases: list[TestCase]) -> TuneResult:
    """等距网格搜索最优权重。

    注意：搜索空间为 5^8 × 4^3 × 4^2 × 4 × 4 = 巨大。
    实际采用随机采样 200 组 + 默认参数的组合。
    """
    import random
    random.seed(42)

    best = _search_and_score(test_cases, _default_params())
    logger.info("default weights score: %.4f", best.score)

    spaces = _param_space()
    # 随机采样代替全网格
    for _ in range(min(200, _total_combinations(spaces))):
        weights = {}
        for i, p in enumerate(_PARAM_DEFS):
            candidate_vals = spaces[i]
            weights[p["name"]] = random.choice(candidate_vals)

        result = _search_and_score(test_cases, weights)
        if result.score > best.score:
            best = result
            logger.info("new best: score=%.4f params=%s", best.score, _format_params(best.params))

    logger.info("grid search done: best_score=%.4f", best.score)
    return best


def bayesian_optimize(test_cases: list[TestCase], n_calls: int = 50) -> TuneResult | None:
    """Bayesian optimization（需 pip install scikit-optimize）。"""
    try:
        from skopt import gp_minimize
        from skopt.space import Real
    except ImportError:
        logger.warning("scikit-optimize not installed, falling back to grid search")
        return None

    dimensions = [
        Real(p["min"], p["max"], name=p["name"])
        for p in _PARAM_DEFS
    ]

    def objective(x):
        weights = {_PARAM_DEFS[i]["name"]: float(v) for i, v in enumerate(x)}
        result = _search_and_score(test_cases, weights)
        return -result.score  # minimize negative score

    logger.info("starting Bayesian optimization (%d calls)...", n_calls)
    res = gp_minimize(objective, dimensions, n_calls=n_calls, random_state=42)

    best_weights = {_PARAM_DEFS[i]["name"]: float(v) for i, v in enumerate(res.x)}
    best = _search_and_score(test_cases, best_weights)
    logger.info("bayesian opt done: best_score=%.4f", best.score)
    return best


def _total_combinations(spaces: list[list[float]]) -> int:
    total = 1
    for s in spaces:
        total *= len(s)
    return total


def _format_params(params: dict) -> str:
    """格式化参数为紧凑字符串（只显示相对默认值的变化）。"""
    defaults = _default_params()
    changes: list[str] = []
    for k, v in params.items():
        d = defaults.get(k, v)
        if abs(v - d) > 0.001:
            changes.append(f"{k}={v:.3f}(Δ{'+' if v > d else ''}{v - d:.3f})")
    return ", ".join(changes) if changes else "(no changes from default)"


def save_weights(result: TuneResult, filepath: str) -> None:
    """保存优化后的权重到 JSON 文件。"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({
            "score": result.score,
            "avg_reciprocal_rank": result.avg_reciprocal_rank,
            "perfect_recalls": result.perfect_recalls,
            "params": result.params,
        }, f, indent=2, ensure_ascii=False)
    logger.info("weights saved to %s", filepath)


# ── CLI ──

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Search ranker parameter tuner")
    ap.add_argument("test_cases", help="JSON file: [{query, expected_ids, top_k?}]")
    ap.add_argument("--evaluate", action="store_true", help="Evaluate default weights")
    ap.add_argument("--tune", action="store_true", help="Run grid search optimization")
    ap.add_argument("--bayesian", action="store_true", help="Use Bayesian optimization")
    ap.add_argument("--output", default="optimized_weights.json", help="Output file")
    args = ap.parse_args()

    with open(args.test_cases, encoding="utf-8") as f:
        raw = json.load(f)
    test_cases = [TestCase(**tc) for tc in raw]

    if args.evaluate:
        result = evaluate(test_cases)
        print(f"Default score: {result.score:.4f} (MRR)")
        print(f"Perfect recalls: {result.perfect_recalls}/{result.test_cases}")

    elif args.tune:
        if args.bayesian:
            result = bayesian_optimize(test_cases) or grid_search(test_cases)
        else:
            result = grid_search(test_cases)
        print(f"Best score: {result.score:.4f} (MRR)")
        print(f"Perfect recalls: {result.perfect_recalls}/{result.test_cases}")
        print(f"Params: {_format_params(result.params)}")
        save_weights(result, args.output)

    else:
        ap.print_help()
