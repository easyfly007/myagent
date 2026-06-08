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
| `run_spice` | 前仿真 | rustspice |
| `draw_layout` | 生成/修改版图 | mylayout |
| `run_drc` | 设计规则检查 | magic / klayout + sky130 规则 |
| `run_lvs` | 版图 vs 原理图 | netgen / klayout |
| `run_pex` | 寄生参数提取 | magic / klayout |
| `run_postsim` | 后仿真（含寄生） | rustspice |
| `read_report` | 读取/解析报告 | 文件操作 |
| `package_artifacts` | 打包结果供下载 | 文件操作 |

> 注：当前探测到 magic / klayout / ngspice 已装；rustspice / mylayout 需确认安装路径与 PDK_ROOT。

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

### 第一层：EDA 领域核心（最关键，目前最模糊，决定产品智能程度）

1. **agent 的设计方法论** —— 「设计电路」目前是黑盒，需定义：
   - 网表来源：LLM 从零写 SPICE / 从**拓扑模板库**选+填参 / 混合？
   - 器件 sizing：LLM 直接给 W/L / 有方法（gm·Id、扫参）/ 优化循环？
   - **设计-仿真-评估-再设计的迭代闭环**：达标判定 + 自动调参重跑——
     这是「会设计电路的 agent」与「会调工具的聊天机器人」的本质区别，**应最先定义**。
2. **测试平台（testbench）来源** —— 仿真需激励源/负载/分析(.ac/.tran)/测量(.meas)；
   由 agent 自动生成 / 模板 / 用户提供？没它仿真跑不起来。
3. **版图自动化程度** —— mylayout 从网表自动生成 / PCell 拼接 / 需布局指导？
   直接决定 agent 能端到端到什么程度。

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
