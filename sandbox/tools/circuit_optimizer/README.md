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

## Evaluator 设计（rustspice 接口已确认）

evaluator 是优化器**唯一**与仿真器耦合的地方，只有两个窄接口，目标仿真器 = 自研 rustspice：

```
engine.ask() → {W1:8u, L1:0.5u, Ibias:50u}
   ① 参数注入：重写 deck 的 .param 块（rustspice 支持 .param，
                器件用 W={W1} 引用；每轮只改 param 块，网表不动）
   ② 组 deck = 网表 + testbench(.meas) → 写 deck_file
   ③ 跑 rustspice 命令（spec.simulation.command）
   ④ 读测量：rustspice .meas 输出结构化结果（JSON/CSV），直接读，无需脆弱文本解析
        → metrics = {gain:38.2, pm:62, power:0.9m}
engine.tell(params, metrics)
   · objectives = metrics[obj.metric]
   · constraints 可行性 = compare(metrics[c.metric], c.op, c.value)
```

> 自循环：ask→注入→rustspice→读测量→tell 全在沙箱内、全靠 rustspice。
> 因 rustspice 自研，两个接口都做到最干净：`.param` 覆盖参数 + `.meas` 结构化输出。
> 关键约束：testbench 的 `.meas` 名必须与 objective/constraint 的 metric 名对齐（§4.7）。

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
3. `evaluator.py` —— `.param` 注入 + 跑 rustspice + 读结构化 `.meas`（接口已确认，见上）
4. `engines/bayesian_ax.py` —— 默认引擎，constrained MOBO
5. `optimizer.py` + `cli.py` —— 串起来，跑通单引擎端到端
6. `engines/evolutionary.py` / `nsga.py` —— 补其他引擎
7. tests

> 状态：骨架已建，核心算法与 evaluator 待实现。
