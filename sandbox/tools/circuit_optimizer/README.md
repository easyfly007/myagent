# circuit_optimizer —— 电路参数优化器

带约束的多目标电路参数优化器。被后端 agent 当作 `run_optimizer` 工具调用，
**运行在沙箱内**（循环反复调 rustspice），见 `../../../ARCHITECTURE.md` §4.6.4。

## 核心设计：可插拔优化引擎

优化引擎做成**可插拔抽象层**——编排循环与具体算法解耦。统一 `Engine` 接口
（ask/tell 模式，Ax/nevergrad/Optuna 通用），底下挂多种算法：

| 引擎 | 算法 | 库 | 适用场景 |
|------|------|----|---------|
| `bayesian_ax` | 贝叶斯 constrained MOBO | Ax/BoTorch | **仿真贵** → 样本高效（默认） |
| `evolutionary` | CMA-ES / DE | nevergrad | 仿真快、可并行、避局部最优 |
| `nsga` | NSGA-II 多目标进化 | pymoo | 要完整 Pareto 前沿、仿真快 |

> **引擎选择准则**：仿真成本决定算法。仿真贵 → 贝叶斯（省评估次数）；
> 仿真快 → 遗传/进化（评估多、易并行、跳出局部最优）。接口为「按场景换算法」而留。

## 包结构

```
circuit_optimizer/
├── spec.py        # 问题定义：Variable / Objective / PerfConstraint / OptimizationSpec
├── evaluator.py   # 渲染网表 → run rustspice → 解析 .meas → 指标；DC 体检
├── engines/
│   ├── base.py        # Engine 抽象接口（ask/tell/is_done/result）
│   ├── __init__.py    # 引擎注册表 + 工厂 get_engine()（按需懒加载，不强依赖重库）
│   ├── bayesian_ax.py # Ax/BoTorch（默认）
│   ├── evolutionary.py# nevergrad
│   └── nsga.py        # pymoo
├── optimizer.py   # 编排：spec → engine + evaluator → result（引擎无关）
├── result.py      # OptimizationResult / EvalRecord / Pareto
└── cli.py         # run_optimizer 工具入口（读 spec JSON → 写 result JSON）
```

## 编排循环（引擎无关）

```python
engine = get_engine(spec.engine, spec)
while not engine.is_done():
    for params in engine.ask():            # 引擎提点（守变量边界+约束）
        metrics = evaluator.evaluate(params)  # run_spice + .meas
        engine.tell(params, metrics)       # 回报目标+约束指标值
return engine.result()                     # Pareto / best / history
```

## 约束两类（见 §4.6.4）

- **变量约束（输入侧）**：`W1==W2`、`L1<=L2`、边界 —— 引擎在搜索空间直接强制。
- **性能约束（黑盒）**：相位裕度≥60° 等 —— 仿真测出后由引擎采集函数/惩罚处理。

## 安装

```bash
pip install -e .                 # 核心
pip install -e ".[bayesian]"     # + Ax/BoTorch（默认引擎）
pip install -e ".[evolutionary]" # + nevergrad
pip install -e ".[nsga]"         # + pymoo
```

## 实现路线（待开发）

1. `spec.py` / `result.py` —— 数据模型（已搭骨架，可序列化）
2. `engines/base.py` —— 抽象接口（已定）
3. `evaluator.py` —— 接 rustspice + `.meas` 解析（待 rustspice 调用方式确认）
4. `engines/bayesian_ax.py` —— 默认引擎，constrained MOBO
5. `optimizer.py` + `cli.py` —— 串起来，跑通单引擎端到端
6. `engines/evolutionary.py` / `nsga.py` —— 补其他引擎
7. tests

> 状态：骨架已建，核心算法与 evaluator 待实现。
