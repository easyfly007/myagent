# sandbox —— 沙箱侧（打进镜像的工具）

每会话隔离的执行环境（见 `../ARCHITECTURE.md` §5）。镜像内含 sky130 PDK、
mylayout、rustspice、magic/klayout/netgen，以及 `tools/` 下的工具包。

- `Dockerfile` —— 沙箱镜像定义（PDK + EDA 工具 + tools/ 包）
- `tools/circuit_optimizer/` —— 电路参数优化器（贝叶斯/进化等可插拔引擎）
- `tools/netlist/` —— 网表模板渲染
- `tools/testbench/` —— testbench 自动组装（测量库 + 接线，§4.7）

这些工具被后端 agent 当作 MCP/CLI 工具调用，循环（如优化器反复 run_spice）
在沙箱内完成，避免后端↔沙箱往返。

> 状态：规划中。当前优先 `tools/circuit_optimizer/`。
