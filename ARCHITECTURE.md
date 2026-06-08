# MyAgent —— 浏览器端 EDA Agent 系统 · 架构设计文档

> 状态：草案 v0.1 · 2026-06-07
> 一个让用户通过对话来设计电路的 agent 系统：用户在浏览器里聊天下指令，
> 后端 agent 调 LLM 编排工具，在隔离 sandbox 里跑 EDA 工具链
> （rustspice 仿真 / mylayout 画版图 / DRC / LVS / PEX / 后仿），结果回显并可下载。

---

## 1. 核心定位（先理清概念）

- **用户界面 = Web GUI**（浏览器里的 chat + 结果展示），未来扩展到桌面 app。
- **Agent = headless 后台服务**：本身没有界面。它跑一个循环：
  收消息 → 调 LLM → LLM 决定调哪个工具 → 在 sandbox 执行 → 结果喂回 LLM → 直到完成。
- **EDA 工具 = sandbox 里的 CLI 程序**：被 agent 当作「工具」调用。

> 形态类比：本系统 ≈ claude.ai 网页版 / Cursor（Web GUI + 后端 agent 服务），
> 不是 Claude Code 那种终端 TUI。

### 决定性架构原则：前端无关的后端

后端做成完全 **frontend-agnostic** 的 headless API（REST + WebSocket）。
前端只是它的一个**可替换客户端**。这样语言选择、前端何时做、何时换，都不绑架后端。

```
        ┌──────────── 后端 (Python / FastAPI) ────────────┐
        │   headless agent API：REST + WebSocket          │
        │   完全不知道前端长什么样                          │
        └───────────────────┬─────────────────────────────┘
                            │  同一套 API
       ┌────────────────────┼──────────────────────┐
       ▼                    ▼                      ▼
   浏览器 (React)      桌面 app (Tauri 包同一份 React)   未来: CLI / 其他客户端
```

---

## 2. 技术栈选型

| 层 | 选择 | 理由 |
|----|------|------|
| 后端 / agent 编排 | **Python + FastAPI** | 团队主力语言；LLM 生态最厚；EDA 数据处理（网表/波形/报告）生态压倒性领先；agent 逻辑迭代快 |
| LLM | **多 provider，用户可选**（见 §4.5） | 支持 Anthropic(Opus/Sonnet/Haiku) / OpenAI(GPT) / DeepSeek；用户在界面选模型 |
| Agent → 工具协议 | **MCP（Model Context Protocol）** | 工具定义清晰、可移植；sandbox 内跑一个 MCP server 暴露 EDA 工具 |
| 前端 | **React + TypeScript** | 业界常规；桌面 app 可复用；chat UI 模板成熟 |
| 前后端通道 | **WebSocket（主）+ REST（辅）** | agent 多步长任务需流式推送中间状态；REST 处理 CRUD/上传下载 |
| 桌面 app（未来） | **Tauri**（或 Electron） | 把同一份 React 前端包成桌面端，几乎零额外前端工作 |
| Sandbox | **容器隔离**（详见 §5） | 每会话隔离运行 EDA 工具链，安全边界 |

> 语言解耦要点：rustspice 是 Rust，但它只是 sandbox 里被调用的一个 CLI；
> 编排层用 Python 不受影响——「编排层」只负责启动进程、传参、读结果。

### 开发顺序建议（后端优先）

1. 先把后端核心链路做扎实（agent 循环 + sandbox + EDA 工具调用）——项目真正的难点与价值。
2. 前端先用「最薄客户端」（脚本/简易页面）验证主链路。
3. 后端成型后再正式做 React 前端。
4. 最后用 Tauri 包出桌面 app。

---

## 3. 系统总览

```
┌───────────────┐    WebSocket / SSE + REST    ┌──────────────────────────────┐
│   浏览器        │ ◄─────────────────────────► │   后端 API 服务 (FastAPI)      │
│  React GUI     │                              │  ┌────────────────────────┐  │
│  - Chat        │                              │  │ 会话管理 / 鉴权 / 多租户 │  │
│  - 结果/波形/版图│                              │  ├────────────────────────┤  │
│  - 下载         │                              │  │ Agent 编排循环           │  │
└───────────────┘                              │  │  - 调 Claude API         │  │
                                                │  │  - 解析 tool_use         │  │
                                                │  │  - 路由到 sandbox 工具    │  │
                                                │  └───────────┬────────────┘  │
                                                └──────────────┼───────────────┘
                                                               │ MCP / RPC
                                              ┌────────────────▼────────────────┐
                                              │   Sandbox（每会话隔离的容器）     │
                                              │   MCP server 暴露工具：          │
                                              │   run_spice / draw_layout /     │
                                              │   run_drc / run_lvs / run_pex / │
                                              │   run_postsim                   │
                                              │                                  │
                                              │   已装环境：                      │
                                              │   - sky130 PDK                   │
                                              │   - mylayout                     │
                                              │   - rustspice                    │
                                              │   - magic / klayout / ngspice    │
                                              │                                  │
                                              │   工作区 /workspace → artifacts  │
                                              └──────────────────────────────────┘
```

---

## 4. Agent 编排循环

后端核心。单个用户回合（turn）的处理：

```
1. 用户消息进入 → 加入会话历史
2. 调 Claude API（带：系统提示 + 历史 + 工具定义列表）
3. Claude 返回：
   a. 纯文本回复  → 流式推给前端，回合结束
   b. tool_use    → 解析出工具名 + 参数
4. 把 tool_use 路由到该会话的 sandbox（经 MCP）执行
5. 工具执行中：把进度（"正在跑 DRC…"）经 WebSocket 推给前端
6. 工具结果（stdout / 报告 / 产物路径）作为 tool_result 喂回 Claude
7. 回到第 2 步，直到 Claude 给出纯文本结论
```

**典型设计流程**（agent 自动编排成一串工具调用）：

```
设计电路网表 → run_spice(前仿) → draw_layout(mylayout)
→ run_drc → run_lvs → run_pex → run_postsim → 打包产物供下载
```

## 4.5 多模型支持（LLM Provider 抽象层）

用户可在界面选择模型（GPT / DeepSeek / Claude Opus 等）。后端通过一层
**Provider 抽象**屏蔽各家差异，agent 编排循环只面对一套统一接口。

```
        agent 编排循环
              │  统一接口: chat(messages, tools) → {text | tool_calls}
              ▼
   ┌──────── LLM Provider 抽象层 ────────────┐
   │  AnthropicProvider  (Opus / Sonnet / Haiku) │
   │  OpenAIProvider     (GPT-4o / GPT-4o-mini)  │
   │  DeepSeekProvider   (deepseek-chat / R1)    │
   └────────────────────────────────────────────┘
```

**核心难点**：各家 tool-calling（函数调用）格式不同——
- Anthropic：`tool_use` / `tool_result` 块
- OpenAI：`tool_calls` / `function`
- DeepSeek：兼容 OpenAI 格式

抽象层负责把这些差异**规范化为一套统一的消息/工具格式**，使切换模型时
agent 逻辑零改动。

**要点**：
- 每个 provider 的 API key 在后端配置，前端永不接触。
- 不同模型工具调用可靠性/成本不同（编排类任务 Opus/GPT-4o 较稳，DeepSeek 成本低）。
- 抽象层是后端核心模块之一，需可插拔以便后续接入新 provider。

### 工具集（MCP tools，初版）

| 工具 | 作用 | 底层 |
|------|------|------|
| `write_netlist` | 写/改 SPICE 网表 | 文件操作 |
| `generate_testbench` | 组装 testbench（测量库+接线，详见 §4.7） | 测量库 + LLM |
| `run_spice` | 前仿真 | rustspice |
| `run_optimizer` | 电路参数优化（贝叶斯/多目标，详见 §4.6） | Ax/BoTorch + rustspice |
| `draw_layout` | 生成/修改版图 | mylayout |
| `run_drc` | 设计规则检查 | magic / klayout + sky130 规则 |
| `run_lvs` | 版图 vs 原理图 | netgen / klayout |
| `run_pex` | 寄生参数提取 | magic / klayout |
| `run_postsim` | 后仿真（含寄生） | rustspice |
| `read_report` | 读取/解析报告 | 文件操作 |
| `package_artifacts` | 打包结果供下载 | 文件操作 |

> 注：当前探测到 magic / klayout / ngspice 已装；rustspice / mylayout 需确认安装路径与 PDK_ROOT。

---

## 4.6 设计方法论（产品的灵魂）

定义 agent「怎么设计电路」。核心原则：**把 LLM 放在它擅长的位置（方法论编排、
读意图、判断方向、解释结果），把精确数值的活交给工具（sizing 方法、数值优化器）。**

### 4.6.1 两个循环：内层纯电路 + 外层含版图

```
┌─── 内层循环：纯电路设计（前仿，无版图，快/便宜，大部分收敛在此）──┐
│   选拓扑/模板 → sizing → run_spice 前仿 → 评估 vs 规格            │
│        ▲                                          │              │
│        └────────── 没达标，调参/换拓扑 ◄───────────┘              │
└──────────────────────────┬───────────────────────────────────────┘
                           │ 电路收敛、前仿达标后，才进版图
                           ▼
┌─── 外层循环：含版图（慢/贵，低频，人工介入更多）─────────────────┐
│   draw_layout → DRC → LVS → run_pex → run_postsim                │
│        ▲                                          │              │
│        └──── 后仿因寄生不达标，回去改尺寸/重画 ◄───┘              │
└──────────────────────────────────────────────────────────────────┘
```

铁律：**版图很贵，绝不在电路未收敛时画版图。** 先在前仿层面把指标调达标，
再进版图；后仿因寄生退化若掉出规格，才触发外层回退。两层的「达标判定」和
「调参策略」不同——内层调电路参数，外层可能调版图或回退电路。

### 4.6.2 网表（拓扑）来源 = A3 混合

- **优化过的模板库**（主路径）：预置常见拓扑（diff amp / OTA / bandgap / 比较器…），
  LLM 选模板 + 填参。模板由 Claude 生成初版 → 人工优化 → 成为可靠、可复现的库，
  也是沉淀模拟设计 know-how 的护城河。
- **LLM 现场生成**（fallback）：模板库未覆盖的拓扑由 LLM 生成，可沉淀为新模板。

### 4.6.3 Sizing = B2 给初值 + B3 优化器收敛 + LLM 掌舵

- **B2 方法算初值**：用 gm/Id 或解析公式给一个有物理依据的起点（好起点省大量仿真）。
- **B3 优化器收敛**：`run_optimizer` 在变量空间里数值搜索逼近目标（见 4.6.4）。
- **LLM 掌舵**：formulate 问题、给边界、读结果、卡住时改边界/换拓扑。

### 4.6.4 优化器：嵌套双层（LLM 战略外层 + 优化器数值内层）

```
┌─ LLM agent（战略层，判断方向）──────────────────────────────────┐
│  规格→形式化目标/约束 · 选变量+边界 · 监控收敛 · 卡住则换策略/拓扑 │
└──────────────────────────┬───────────────────────────────────────┘
                           │ 调用 run_optimizer
                           ▼
┌─ Optimizer（数值层，系统搜索）─────────────────────────────────┐
│  选点 → run_spice → .meas 测指标 → 更新代理模型 → 再选点         │
│  直到达标 / 收敛 / 用尽仿真预算                                  │
└──────────────────────────────────────────────────────────────────┘
```

- 优化器**改不了拓扑**，只能在给定变量空间搜；换拓扑是 LLM 的战略判断。
- **引擎 = 贝叶斯优化（Ax/BoTorch）**：每次评估 = 一次 SPICE 仿真，很贵，
  所以样本效率第一 → 用代理模型的贝叶斯最优；包成工具，**不自己造算法**。
- **多目标 + 多约束（constrained MOBO）**：用 qNEHVI 类采集函数直接搜 Pareto 前沿，
  同时满足约束。

#### 约束分两类（处理方式不同）

| 类型 | 例子 | 处理 | 需仿真 |
|------|------|------|--------|
| **变量约束（输入侧）** | `W∈[1u,50u]`、`W1==W2`（差分对匹配）、`L1≤L2`、`W*L≤X` | 搜索空间/参数约束直接强制，省仿真 | 否 |
| **性能约束（输出侧/黑盒）** | 相位裕度≥60°、功耗≤1mW、管子在饱和、输出摆幅、CMRR、压摆率 | outcome constraint，仿真后判定，违反点被采集函数惩罚/排除 | 是 |

完整问题形式：
```
maximize/minimize  [ gain, -power, ... ]              ← 多目标 → Pareto
subject to
   性能约束:  phase_margin ≥ 60°, power ≤ 1mW, 全管饱和   ← 黑盒，仿真判定
   变量约束:  W1 == W2, 1u ≤ W ≤ 50u, L1 ≤ L2            ← 已知，搜索前强制
```

#### `run_optimizer` 工具契约

```
run_optimizer(
  netlist_template,        # 带可调参数占位符的网表
  variables:   { W1:[1u,50u], L1:[0.15u,2u], Ibias:[10u,500u], ... },
  var_constraints: [ "W1==W2", "L1<=L2", "W1*L1<=X" ],   # 输入侧，搜索前强制
  objectives:  [ maximize(gain), minimize(power) ],       # 多目标
  perf_constraints: [ "phase_margin>=60", "power<=1mW", "all_in_saturation" ],  # 黑盒
  init_point:  { ... },    # B2 gm/Id 给的好起点
  budget:      100,        # 仿真次数上限（控成本）
) → { pareto: [...], best: {...}, met_spec: bool, history: [...] }
```

LLM 拿到结果后：达标 → 解读 Pareto 权衡报告用户；没达标 → 推理原因
（如「增益受限于 gm，放大输入对管 W 上界 / 换两级拓扑」）→ 重新 formulate 再优化。
**这层「读结果→重新设定→再优化」的战略循环，就是产品智能的核心。**

### 4.6.5 迭代模式 = 人在环路（默认）+ 自动优化（可选）

- **人在环路（默认）**：agent 跑一轮 → 展示结果 → 用户用自然语言指挥下一步。
- **自动优化（可选）**：用户开启后，agent 调 `run_optimizer` 自动迭代至达标。
  慢且烧算力，受 `budget` 仿真次数上限约束。

---

## 4.7 Testbench 自动生成

agent 自动生成 testbench，但**建立在「验证过的测量库」之上**（混合：库优先、缺失才生成）。

### 关键风险（为什么不纯自由生成）

testbench 里最致命的是测量语句 `.meas`（增益/相位裕度/UGB/CMRR/压摆率怎么从波形精确量出）。
**若 `.meas` 写错，仿真照样跑通、照样出一个数，但这个数是错的——优化器会拿错数当目标
拼命优化，garbage in / garbage out，且静默不报错。** 比网表错误更危险（网表错常不收敛、看得见）。

### 设计：agent 自动「组装」，不徒手写 `.meas`（混合 = A3 思路）

```
验证过的测量库（每个都是测试过的参数化 SPICE 片段）：
  gain.meas / ugb.meas / phase_margin.meas / cmrr.meas /
  slew_rate.meas / power.meas / offset.meas / noise.meas ...
  —— 库未覆盖的罕见指标，才由 LLM 现场生成，并沉淀为新积木

agent 自动生成 testbench 时做的事（LLM 擅长的「意图→结构」映射）：
  1. 识别电路端口角色：in+/in-/out/vdd/vss/bias
  2. 接激励源 + 负载 + 偏置（按电路类型）
  3. 选分析类型：.ac（增益/带宽/PM）/ .tran（压摆/建立）/ .dc（失调）/ .noise
  4. 从测量库挑「用户规格要的指标」对应的 .meas 片段，填好节点名
  → 组装成完整 testbench
```

**分工**：LLM 干「识别端口、接线、选分析、挑测量、填节点名」；**不让它徒手写 `.meas` 公式**。

### 三个衔接点

1. **testbench 与优化器目标/约束一起生成**：用户规格 → 同时产出
   优化器的 `objectives/constraints` + 一个 `.meas` 名字与之对齐的 testbench
   （优化器才读得到 `gain`/`phase_margin`/`power`）。
2. **端口角色元数据**：模板电路自带；LLM 生成的网表需顺带声明端口角色，才能接线。
3. **开跑优化前先 DC 体检**：组装后先跑一次廉价 DC 工作点仿真，查浮空节点/管子全关断等
   低级错误，确认后再启动昂贵的优化循环。

### 完整链路

```
用户规格 → 选/生成网表(模板库 §4.6.2)
        → 组装 testbench(测量库+接线 §4.7) + 同步产出优化目标/约束
        → DC 体检 → run_optimizer(内层反复 run_spice + .meas)
        → LLM 解读 Pareto / 没达标则重新 formulate
```

---

## 5. Sandbox 隔离（安全核心）

每个用户会话对应一个隔离的执行环境，运行不可信的设计任务与 EDA 工具。

### 隔离方案对比

| 方案 | 隔离强度 | 启动延迟 | 成本/复杂度 | 适用 |
|------|---------|---------|-----------|------|
| **Docker 容器** | 中（共享内核） | 快（~秒） | 低 | MVP 首选 |
| **gVisor**（容器+用户态内核） | 高 | 中 | 中 | 加固后默认 |
| **Firecracker microVM** | 最高（独立内核） | 中（~百ms-秒） | 高 | 强多租户/生产 |

**建议**：MVP 用 Docker（每会话一容器）；上线前升级 gVisor 或 Firecracker。

### 关键设计点
- **每会话一沙箱**，`/workspace` 为工作目录，PDK 只读挂载。
- **资源限额**：CPU / 内存 / 磁盘 / 执行超时（防失控仿真）。
- **网络隔离**：沙箱默认无出网（LLM 调用在后端发生，不在沙箱内）。
- **生命周期**：会话开始拉起 / 空闲回收 / 结束清理；可考虑池化预热降延迟。
- **产物落地**：结果写入持久卷或对象存储，供下载，与沙箱生命周期解耦。

---

## 6. 前后端数据流 & 接口（初版）

### REST（CRUD / 文件）
- `POST /sessions` 创建会话（拉起 sandbox）
- `GET  /sessions/{id}` 会话状态
- `POST /sessions/{id}/messages` 发消息（也可走 WS）
- `GET  /sessions/{id}/artifacts` 列出产物
- `GET  /sessions/{id}/artifacts/{name}` 下载产物
- `DELETE /sessions/{id}` 结束并清理

### WebSocket（流式）
- 客户端 → 服务端：用户消息、取消请求
- 服务端 → 客户端：assistant 文本增量、工具调用开始/进度/结束、波形/版图预览就绪、错误

### 事件类型（草案）
```
text_delta        # assistant 文本流
tool_start        # {tool, args}
tool_progress      # {tool, message}
tool_result        # {tool, summary, artifact_refs}
preview_ready      # {kind: waveform|layout, url}
turn_done
error
```

---

## 6.5 界面展示设计（不止聊天：波形 / 版图 / 报告）

界面不是单一聊天框，而是**「对话 + 工作区」双栏布局**（业界常规：Claude Artifacts / Cursor 模式）。

> **重要定位：工作区是「只读查看器」，不是编辑器。**
> 所有设计/修改都通过对话由 agent 在 sandbox 内完成；浏览器只负责「看」结果
> （波形 / 版图 / 报告），用户不在浏览器里拖拽版图或连线。
> 想改 → 在聊天里说「把 W 调大」→ agent 改并重新产出图 → 右栏刷新。
> 这是 **agent-first** 范式：人表达意图，agent 执行，界面只反映结果。
> 好处：无需重型图形编辑器；前端单向（sandbox→前端，只推不收）、近乎无状态、非常薄，
> React 入门级即可胜任。

```
┌──────────────────────────────────────────────────────────┐
│  MyAgent                                    [下载产物 ▼]   │
├────────────────────────┬─────────────────────────────────┤
│   对话区 (Chat)         │   工作区 / 预览区 (Canvas)        │
│  - 消息流              │   [波形] [版图] [报告] tab 切换    │
│  - 工具状态 ⟳ ✓        │   ← 波形图 / 版图 / DRC·LVS 报告  │
│  [输入框]              │     在此显示，随 agent 产出更新    │
└────────────────────────┴─────────────────────────────────┘
```

- 左栏：对话 + 工具调用状态（正在跑 DRC… ✓ 完成）。
- 右栏：tab 切换显示 **波形 / 版图 / 报告**；agent 每产出一个图就更新。
- 两栏经同一条 WebSocket 联动：工具产出图 → `preview_ready` 事件 → 右栏自动加载并切 tab。

### 图怎么显示：两个层次（先静态，后交互）

| 方式 | 实现 | 优点 | 缺点 | 阶段 |
|------|------|------|------|------|
| **静态图片** | sandbox 渲成 PNG/SVG，后端给 URL，前端 `<img>` 显示 | 简单稳，MVP 够用 | 不可交互 | MVP |
| **交互式查看器** | 波形用前端图表库（可缩放/游标）；版图用 KLayout web 预览或导出 GDS 矢量 | 可缩放/量取/专业 | 复杂，需传结构化数据 | 后期 |

> 升级路径：波形交互化时，由 Python 后端出结构化数据点、React 前端用图表库渲染——
> 正好各发挥所长。

### 展示数据流（一张波形：sandbox → 浏览器）

```
sandbox: rustspice 仿真 → 输出数据(.raw) → 渲染 waveform.png 存入 /workspace
   ↓ 工具返回 artifact 路径
后端: 收 tool_result → 注册 artifact 为可访问资源 → 得到 URL
   ↓ WebSocket 推 preview_ready{kind:waveform, url:.../artifacts/waveform.png}
浏览器: 右栏工作区收事件 → 切到「波形」tab → <img src=url> 显示
```

---

## 7. 安全边界小结

- **沙箱无出网**：不可信代码进不了外网；LLM API 调用只在后端发生。
- **后端持密钥**：Anthropic API key 等只在后端，前端永不接触。
- **资源限额 + 超时**：防 DoS / 失控仿真。
- **下载即导出**：用户产物显式打包导出；注意多租户间产物隔离。
- **鉴权 / 多租户**：会话与用户绑定，沙箱与产物按租户隔离。

---

## 8. 里程碑路线图（建议）

- **M0 主链路打通**：后端 agent 循环 + 单沙箱 + 跑通 `run_spice` 一个工具，命令行/最薄客户端验证。
- **M1 工具链补全**：补 draw_layout / DRC / LVS / PEX / 后仿，串成完整流程。
- **M2 沙箱加固**：资源限额、网络隔离、生命周期管理、产物持久化。
- **M3 React 前端**：正式 chat GUI + 波形/版图展示 + 下载。
- **M4 多租户 & 鉴权**：**用户注册 / 登录**、用户系统、会话隔离、配额。
  - 用户注册功能（账号注册、登录、会话与用户绑定）—— 待实现，后续补充。
- **M5 桌面 app**：Tauri 打包 React 前端。

---

## 9. 待细化 / 开放问题（按重要性分三层）

界面已确定「系统能做什么」，但「做得好不好、智不智能」「能不能稳定运行」的关键
细节大多尚未决策。下面按优先级分三层；**建议优先钉死第一层 + 第二层第 4 点**。

### 第一层：EDA 领域核心（最关键，决定产品智能程度）

1. ✅ **agent 的设计方法论** —— 已定，见 §4.6（A3 模板库 + B2/B3+LLM 掌舵 +
   constrained MOBO 优化器 + 双循环 + 人在环路/自动优化）。
2. ✅ **测试平台（testbench）来源** —— 已定，见 §4.7（agent 自动组装，
   验证过的测量库优先、缺失才 LLM 生成；与优化器目标/约束一起生成；DC 体检）。
3. **版图自动化程度** —— mylayout 从网表自动生成 / PCell 拼接 / 需布局指导？
   直接决定 agent 能端到端到什么程度。**（待讨论）**

### 第二层：平台 / 基础设施（架构级，影响后端骨架，越早定越好）

4. **异步任务模型** —— 后仿/DRC 耗时数分钟，agent 循环不能阻塞。需：
   提交 job → 流式/轮询进度 → 取结果；并处理**断线重连到运行中任务**。
   *（不解决会导致后端推倒重来。）*
5. **设计状态 & 版本管理** —— 改参重跑后旧网表/旧波形是否保留？支持对比/回退？
   迭代设计天然需要项目文件的版本快照。
6. **数据持久化模型** —— DB 存什么（用户/会话/消息/产物元数据）vs 文件系统存什么
   （`/workspace`）？需要一套 schema。
7. **Sandbox 生命周期细节** —— 冷启动延迟、池化预热、空闲回收、workspace 跨重连持久化。
8. **沙箱隔离强度选型** —— MVP 用 Docker 是否可接受，还是一开始就上 gVisor/Firecracker？

### 第三层：运营 / 商业（上线前必须，可暂缓）

9. **成本 & 配额** —— agent 多步循环烧 token、仿真烧算力；每用户 token/算力配额与限流。
10. **可观测性** —— 记录 agent 决策与工具调用轨迹，便于排查「agent 行为异常」。
11. **下载产物格式** —— 导出包内容（GDS + 网表 + testbench + 各报告 打包？）。
12. **产物存储** —— 本地卷 vs 对象存储（S3 兼容）。
13. **多模型差异** —— 各 provider 工具调用可靠性/成本差异、key 管理、默认模型策略。

### 待确认的环境事实

- rustspice / mylayout 的安装路径、调用方式、PDK_ROOT？（当前 PATH 仅探测到 magic/klayout/ngspice）
