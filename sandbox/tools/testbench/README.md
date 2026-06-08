# testbench —— testbench 自动组装

agent 自动组装 testbench（见 `../../../ARCHITECTURE.md` §4.7）。混合策略：
**验证过的测量库优先，缺失才 LLM 生成**。

- `measurements/` —— 验证过的参数化 `.meas` 积木库
  （gain / ugb / phase_margin / cmrr / slew_rate / power / offset / noise…）
- 组装逻辑：识别端口角色 → 接激励/负载/偏置 → 选分析（.ac/.tran/.dc/.noise）
  → 从库挑出规格要的指标对应 `.meas` → 填节点名 → 完整 testbench
- 与优化器目标/约束**一起生成**，指标名对齐（优化器才读得到）

> 状态：规划中。优化器（`../circuit_optimizer/`）的 evaluator 会消费这里产出的
> testbench 与 `.meas` 指标名。
