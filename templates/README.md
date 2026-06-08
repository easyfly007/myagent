# templates —— 优化过的拓扑模板库

预置常见模拟拓扑（diff amp / OTA / bandgap / 比较器 / 电流镜…），供 agent
选模板 + 填参（见 `../ARCHITECTURE.md` §4.6.2，网表来源 A3 混合）。

每个模板应携带：
- 参数化网表（可调器件尺寸用占位符）
- **端口角色元数据**（in+/in-/out/vdd/vss/bias），供 testbench 自动接线（§4.7）
- 可调变量及合理边界的建议（供优化器/初值用）

模板由 Claude 生成初版 → 人工优化 → 沉淀为可靠、可复现的库。

> 状态：规划中，待 rustspice/mylayout 网表格式确认后填充。
