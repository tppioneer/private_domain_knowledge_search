"""混合检索调优工具包。

- diff_analyzer: 预期 vs 实际代码差异分析，分类错误并定位根因
- replay_validator: 回放检索结果，判定 recall/ranking/format 瓶颈
- param_tuner: Bayesian optimization 调优 ranker 权重参数
"""

from .diff_analyzer import ErrorReport, ImportError, APIError, analyze
