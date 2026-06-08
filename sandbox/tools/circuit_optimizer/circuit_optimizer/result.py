"""优化结果数据模型。cli.py 把它序列化为 JSON 返回给 agent。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class EvalRecord:
    """一次评估（一次仿真）的记录。"""
    params: dict[str, float]      # 该次的变量取值
    metrics: dict[str, float]     # .meas 测出的所有指标（目标 + 约束指标）
    feasible: bool                # 是否满足全部性能约束
    iteration: int


@dataclass
class OptimizationResult:
    """优化结果。

    pareto: 可行（满足约束）且非支配的点集（多目标 Pareto 前沿）。
    best: 单个代表点（如按主目标或超体积贡献挑选），可能为 None。
    met_spec: 是否找到满足全部约束的点。
    history: 全部评估记录（收敛分析 / 复现）。
    """
    pareto: list[EvalRecord] = field(default_factory=list)
    best: EvalRecord | None = None
    met_spec: bool = False
    history: list[EvalRecord] = field(default_factory=list)
    engine: str = ""
    n_sims: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, **kw: Any) -> str:
        return json.dumps(self.to_dict(), **kw)
