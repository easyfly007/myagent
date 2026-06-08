"""circuit_optimizer —— 带约束的多目标电路参数优化器（可插拔引擎）。

被 agent 当 run_optimizer 工具调用，运行在沙箱内。详见 README.md 与
../../../ARCHITECTURE.md §4.6.4。
"""

__version__ = "0.0.1"

from .spec import OptimizationSpec, Variable, Objective, PerfConstraint, Sense
from .result import OptimizationResult, EvalRecord

__all__ = [
    "OptimizationSpec",
    "Variable",
    "Objective",
    "PerfConstraint",
    "Sense",
    "OptimizationResult",
    "EvalRecord",
]
