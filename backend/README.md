# backend —— FastAPI + agent 编排 + LLM provider 抽象

后端 headless 服务。职责（见 `../ARCHITECTURE.md` §3–§4.5）：

- `api/` —— FastAPI 路由：REST（会话/产物 CRUD）+ WebSocket（流式事件）
- `agent/` —— agent 编排循环：调 LLM → 解析 tool_use → 路由到 sandbox → 喂回结果
- `llm_providers/` —— 多 provider 抽象层（Anthropic / OpenAI / DeepSeek），
  统一接口屏蔽各家 tool-calling 格式差异

> 状态：规划中，尚未实现。当前优先开发 `../sandbox/tools/circuit_optimizer/`。
