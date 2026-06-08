"""优化引擎抽象接口。

所有引擎实现统一的 **ask/tell** 接口（Ax / nevergrad / Optuna 通用；pymoo 可包装），
使编排循环（optimizer.py）与具体算法解耦——这就是「按场景换算法」留的接口：
仿真贵 → 贝叶斯（样本高效）；仿真快 → 遗传/进化（评估多、可并行、避局部最优）。

约束处理（见 ../../../ARCHITECTURE.md §4.6.4）：
- 变量约束（spec.var_constraints，输入侧）：引擎在 __init__ 构建搜索空间时强制。
- 性能约束（spec.perf_constraints，黑盒）：通过 tell() 收到的 metrics 判定，
  由引擎的采集函数（贝叶斯）或惩罚（进化）处理。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..spec import OptimizationSpec
from ..result import OptimizationResult


class Engine(ABC):
    """优化引擎基类。子类在 engines/ 下实现，经 get_engine() 工厂创建。"""

    def __init__(self, spec: OptimizationSpec) -> None:
        self.spec = spec

    @abstractmethod
    def ask(self, n: int = 1) -> list[dict[str, float]]:
        """提出 n 个候选点（变量名→取值），需满足变量边界与变量约束。"""
        raise NotImplementedError

    @abstractmethod
    def tell(self, params: dict[str, float], metrics: dict[str, float]) -> None:
        """回报某候选点的评估结果（含目标与性能约束对应的指标值）。"""
        raise NotImplementedError

    @abstractmethod
    def is_done(self) -> bool:
        """是否应停止（达到 budget 仿真次数 / 收敛 / 达标）。"""
        raise NotImplementedError

    @abstractmethod
    def result(self) -> OptimizationResult:
        """返回最终结果（Pareto 前沿 / best / history）。"""
        raise NotImplementedError
