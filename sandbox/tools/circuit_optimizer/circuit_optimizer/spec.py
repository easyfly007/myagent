"""优化问题定义（数据模型）。

agent 从用户的电路规格构造 OptimizationSpec；cli.py 从 JSON 解析它（run_optimizer
工具接口）。详见 ../../../ARCHITECTURE.md §4.6.4（约束两类）与 §4.7（指标名对齐）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Sense(str, Enum):
    """优化方向。"""
    MIN = "minimize"
    MAX = "maximize"


@dataclass
class Variable:
    """一个可调设计变量（输入侧搜索维度）。

    log_scale: 宽度/电流等跨数量级的量，在对数空间搜索通常更好。
    """
    name: str
    low: float
    high: float
    log_scale: bool = False


@dataclass
class Objective:
    """一个优化目标。多个目标 → Pareto（MOBO）。

    metric 必须与 testbench 产出的 .meas 指标名一致（§4.7 指标名对齐）。
    """
    metric: str
    sense: Sense


@dataclass
class PerfConstraint:
    """性能约束（黑盒 / 输出侧），由仿真测量后判定。§4.6.4。

    例：PerfConstraint("phase_margin", ">=", 60)
    """
    metric: str
    op: str          # ">=", "<=", "==", ">", "<"
    value: float


@dataclass
class Simulation:
    """如何跑仿真 + 如何取回指标 —— 让优化器与具体仿真器解耦。

    优化器每次评估：把当前变量值代入 deck（网表+testbench）→ 写到 deck_file →
    运行 command → 从 output_file 按 output_format 解析出指标 dict。
    command/output 都在 spec 里给定，故不绑死 rustspice，任何仿真器皆可。

    command 支持占位符：{deck}（渲染后的 deck 文件路径）、{out}（输出文件路径）。
    例：  "rustspice {deck} --meas {out}"
    """
    command: str                       # 运行仿真的命令（含 {deck}/{out} 占位符）
    deck_file: str = "deck.spice"      # 渲染后的完整 deck 写到此（相对 workdir）
    output_file: str = "sim.meas"      # 命令产出的指标文件（相对 workdir）
    output_format: str = "spice_meas"  # 解析格式：spice_meas / json / csv
    workdir: str = "."
    timeout_s: int = 120


@dataclass
class OptimizationSpec:
    """完整问题定义 —— 自包含，优化器据此即可独立运行（不依赖 agent）。

    netlist_template / testbench: 网表（DUT，带 {变量名} 占位符）与 testbench
                     （激励/负载/.meas）。二者渲染后组成 deck 交给仿真命令。
    variables: 可优化变量。
    objectives: 优化目标（多个 → Pareto）。
    var_constraints: 变量约束（输入侧，"已知约束"），如 "W1==W2"、"L1<=L2"——
                     引擎在搜索空间直接强制，不浪费仿真。
    perf_constraints: 性能约束（黑盒），仿真后判定。
    simulation: 如何跑仿真 + 读指标（见 Simulation）。
    engine: 优化引擎名，见 engines/ 注册表（默认贝叶斯；仿真快时可换进化算法）。
    budget: 最大仿真次数（控成本）。
    """
    netlist_template: str                       # 网表（DUT），带参数占位符
    testbench: str                              # testbench（激励/负载/.meas）
    variables: list[Variable]
    objectives: list[Objective]
    simulation: Simulation
    var_constraints: list[str] = field(default_factory=list)
    perf_constraints: list[PerfConstraint] = field(default_factory=list)
    init_point: dict[str, float] | None = None  # B2 (gm/Id) 给的好起点
    budget: int = 100
    engine: str = "bayesian_ax"

    # ---- (de)serialization：spec 文件 (JSON) 接口，独立运行的入口 ----

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OptimizationSpec":
        return cls(
            netlist_template=d["netlist_template"],
            testbench=d["testbench"],
            variables=[Variable(**v) for v in d["variables"]],
            objectives=[
                Objective(metric=o["metric"], sense=Sense(o["sense"]))
                for o in d["objectives"]
            ],
            simulation=Simulation(**d["simulation"]),
            var_constraints=list(d.get("var_constraints", [])),
            perf_constraints=[PerfConstraint(**c) for c in d.get("perf_constraints", [])],
            init_point=d.get("init_point"),
            budget=int(d.get("budget", 100)),
            engine=d.get("engine", "bayesian_ax"),
        )

    @classmethod
    def from_json(cls, s: str) -> "OptimizationSpec":
        return cls.from_dict(json.loads(s))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, **kw: Any) -> str:
        return json.dumps(self.to_dict(), **kw)
