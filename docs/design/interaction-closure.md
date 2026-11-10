# 交互原地闭环契约（批次 3 · 前端 5 件）

状态：**已落地（2026-11-10，落地位置见 §8）**。覆盖四块：消息操作（重生成 / 编辑重发，含服务端 API）、
会话内计划编辑、代码高亮、错误边界。实现分三处：agent-orchestrator（A 的服务端）、
Web 对话区（A 的前端 + B）、console-ui 与 Web 壳（C + D）。

上游背景：[../gap-2026-11/00-refresh-2026-11-09.md](../gap-2026-11/00-refresh-2026-11-09.md) §5.2 P1-I、§6 前端缺口。

## 1. 目标与非目标

**目标**：把「复制/重生成/编辑重发、计划编辑、代码可读性、白屏兜底」这些用户每天都会用到的
原地闭环补齐，交互不再需要跳到 `/runs` 或重新开会话。

**非目标**：**不做**消息删除 UI（服务端只有「删除最后一轮再重跑」一条路径）、**不做**任意历史轮
重新生成（只开放最后一轮，避免级联删消息与旧 run 语义）、**不做**流式重连/断点续传、**不做**
编辑器级 plan 富文本（纯 markdown 文本域）、**不引入**代码高亮依赖（手写轻量 tokenizer）。

## 2. A. 消息重生成 / 编辑重发

### 2.1 服务端 API（agent-orchestrator）

`POST /api/v1/conversations/{conversation_id}/messages/{message_id}/rewrite`：

- 响应 `text/event-stream`，帧与 `POST /api/v1/conversations/{conversation_id}/messages/stream`
  **完全一致**（`meta` / `delta` / `tool` / `done` / `error`），SSE 头沿用 `SSE_HEADERS`。
- 请求体 `RewriteMessage { content: str | null }`（`max_length=2000`）：
  - 目标 `message.role == "user"`：`content` 必填（1..2000）→ 否则 422 `conversation.content_required`。
  - 目标 `message.role == "assistant"`：`content` 必须省略/为 null → 否则 422 `conversation.content_not_allowed`。
- **只允许最后一轮**：
  - user 目标：必须是该会话**最后一条 user 消息**，且其后最多一条消息、该消息 `role == "assistant"`；
  - assistant 目标：必须是该会话**最后一条消息**（created_at 最大）。
  - 否则 409 `conversation.message_not_latest`。
- **终态校验**：若将被删除的消息（编辑重发时是目标 user 消息之后的消息；重生成时是目标 assistant
  消息）挂着 `run_id`，且该 run 不属于终态（`completed/failed/cancelled/blocked`）→ 409
  `conversation.run_active`（先等 run 结束或取消，避免回放与在飞执行互相踩）。run 到终态后保留，
  不删除 run 与事件（历史仍在 `/runs` 可查）。
- **动作语义**：
  1. user 目标（编辑重发）：用 `content` 覆盖该消息正文，删除其后所有消息（通常是一条 assistant），
     原附件、MCP 引用（`mcp_server_ids`）保持；
  2. assistant 目标（重生成）：删除该 assistant 消息，复用其前一条 user 消息的正文、附件与 MCP 引用；
  3. 复用轮次重新分流（mode = `auto`）、不继承原消息的 skill 选择与模型覆盖（走会话默认模型），
     并如常走 chat 流式 / 工具循环 / 写确认 / hooks / 压缩 / 记忆抽取的既有逻辑；
  4. `meta` 帧里的 `message` 是复用（编辑时是更新后）的 user 消息；`done.reply` 是本次新落库的
     assistant（任务路径为 null）。
- **额度闸**：与 `/messages/stream` 相同，在返回 `StreamingResponse` 之前
  `run_quota.enforce_monthly_quota(account_id)`（429）。
- **实现方式（必须）**：把 `services/conversations_messages.py::stream_message` 的「第二段」抽成
  内部 `_stream_turn(factory, graph, llm, account_id, conversation, message, content, resolved_model, ...)`，
  `stream_message` 保持现有签名与行为不变（只负责持久化新 user 消息 + 分流后调 `_stream_turn`）；
  新增 `stream_rewrite(...)` 做校验/删除后调同一个 `_stream_turn`。禁止复制两份流式/工具循环逻辑。
- 事务：校验 + 覆盖 + 删除在首段 `session_scope` 内完成；流式段不占事务；`done` 后记忆抽取沿用
  独立短事务。
- 不新增 run 事件类型、不写 `audit_logs`、不需要迁移。

### 2.2 前端（apps/web 对话区）

- `lib/conversations-api.ts`：新增 `rewriteMessage(conversationId, messageId, content?)`，
  内部复用现有 SSE 解析（与 `streamMessage` 同一条路径，抽公共 helper，不要复制解析器）。
- `features/chat/message-bubbles.tsx`：`UserBlock` / `AgentBlock` 增加 hover 操作行（桌面 hover、
  触摸端常显），只在「最后一轮」出现：
  - user 消息（最后一条 user）：`复制`、`编辑重发`；
  - assistant 消息（最后一条消息）：`复制`、`重新生成`。
  - 复制用 `navigator.clipboard.writeText`；失败就地提示「复制失败」，不抛错不白屏。
- 编辑重发：气泡就地切换为 `textarea`（预填原文，1..2000，取消/确认），确认后走 rewrite 流；
  流期间禁用发送与其它消息操作；成功后 invalidate 消息、会话列表与当前 run；失败就地中文展示
  （`conversation.*` 错误码给对应文案）。
- 重新生成：点击后直接走 rewrite 流（同 busy 处理）。
- 流式增量复用现有 `use-chat-stream` 的帧处理（`meta/delta/tool/done/error`），保证正文边产出边渲染；
  `tool` 帧照常渲染工具卡。

## 3. B. 会话内计划编辑

- `features/chat/approval-block.tsx::PlanApprovalCard` 增加「编辑计划」按钮（`status == awaiting_approval`
  才显示）：展开 `textarea`（预填 `plan.plan_markdown`，1..20000），`保存计划` 调**现有**
  `PATCH /api/v1/runs/{run_id}/plan`（`runsApi.updatePlan` 已存在，不改后端），成功后 invalidate
  `run/plan`、收起编辑态；失败就地中文提示（409/422）。
- 保存后可继续「放行执行 / 拒绝」；计划正文为空时允许直接放行（沿用现有占位文案）。

## 4. C. 代码高亮（packages/console-ui）

- `packages/console-ui/src/markdown.tsx` 的围栏代码块渲染接 `highlightCode(code, language)`：
  产出 `{ key, text, kind }[]` 或等价片段，由渲染层拼 `<span className>`，**不用** raw HTML、
  不破坏 `rehype-sanitize`。
- 新增 `packages/console-ui/src/markdown-highlight.ts`：轻量 tokenizer，支持
  `ts/tsx/js/jsx/json/python/py/bash/sh/shell` 的关键字、字符串、注释、数字；其它语言/无语言原样返回。
  高亮 token 用既有 `--ha-*` 令牌对应的 Tailwind 类（`text-ha-accent-soft` 关键字 /
  `text-ha-success` 字符串 / `text-ha-faint` 注释 / `text-ha-warning` 数字），与暗色控制台一致。
- 行内 `code` 不变；流式未闭合代码块不崩（tokenizer 容错）。
- 单测：至少覆盖 语言识别、字符串内关键字不被误判、注释、未知语言、空串。

## 5. D. 错误边界（apps/web 壳）

- 新增 `apps/web/src/components/error-boundary.tsx`：class 组件，`getDerivedStateFromError` 记错误、
  `componentDidCatch` 调 `Sentry.captureException`（`@sentry/react` 未初始化时是 no-op）；
  fallback 展示标题、错误摘要（脱敏：只显示 `error.message`，栈不上屏）、「重新加载」按钮
  （`window.location.reload()`）与「返回工作台」链接（`/`）。
- `apps/web/src/app.tsx`：用 `<ErrorBoundary>` 包 `<Routes>`（保留 `<Suspense fallback={<RouteFallback />}>`），
  任何懒加载页/渲染异常不再整页白屏；单个页面崩溃时外壳保留。
- 单测：子组件抛错时渲染 fallback、正常子组件时不渲染 fallback、Sentry 捕获被调（mock）。

## 6. 验证口径

- 服务端：`npx nx lint agent-orchestrator` + `npx nx test agent-orchestrator`（覆盖率 ≥82），
  `tests/snapshots/openapi.json` 重生成后 prettier；测试零网络（FakeLLM/FakeTool/FakeSandbox 既有夹具）。
- 前端：`npx nx lint @beauty/web` + `npx nx typecheck @beauty/web` +
  `npx nx test @beauty/web -- --run`；console-ui：`npx nx test @beauty/console-ui -- --run` + build。
- 全仓：`bash scripts/check-docs.sh`、`bash scripts/check-workspace.sh`、`git diff --check`。

## 7. 残余（预期）

- 只开放最后一轮：更早的历史轮次仍需新开会话；不对旧 run 做级联删除/取消。
- 编辑重发不继承 skill / 模型覆盖（走会话默认）；任务路径重发会新建 run（旧 run 保留）。
- 高亮是浅 tokenizer（非语法树），复杂模板字符串/JSX 嵌套可能有色差；不处理 diff 块。
- 错误边界只兜 React 渲染错误，不兜事件处理器里的异步异常（仍走 `http.ts` 统一错误条）。

## 8. 落地位置与偏离

### 8.1 服务端（agent-orchestrator）

- 端点：`services/agent-orchestrator/agent_orchestrator/api/v1/conversation_messages.py`
  （`POST /api/v1/conversations/{conversation_id}/messages/{message_id}/rewrite`，SSE；
  开流前 `enforce_monthly_quota` → `precheck_rewrite` → `StreamingResponse`）。
- 服务层：`agent_orchestrator/services/conversations_messages.py` —— `stream_message` 第二段抽成共用
  `_stream_turn(...)`、新增 `_prepare_turn(...)`（分流 + chat 提示词/task 建 run）、`RewritePlan`、
  `_rewrite_plan(...)`、`precheck_rewrite(...)`、`stream_rewrite(...)`；`services/conversations.py` 重导出。
- Schema：`agent_orchestrator/schemas/conversations.py::RewriteMessage{content: str|null}`（`max_length=2000`）。
- 测试：`services/agent-orchestrator/tests/test_conversation_rewrite.py`（9 例）；openapi 快照 +109 行。

### 8.2 Web 对话区（apps/web）

- 数据层：`apps/web/src/lib/conversations-api.ts::rewriteMessage`、`apps/web/src/lib/conversation-stream.ts`
  （`streamTurn` + `parseSseFrame`，与 `/messages/stream` 共用一套解析）。
- 交互：`features/chat/message-actions.tsx`（操作行 + 就地编辑）、`message-bubbles.tsx`、`message-list.tsx`、
  `use-chat-rewrite.ts`、`stream-handlers.ts`、`rewrite-error.ts`、`use-chat-stream.ts`、`use-chat-actions.ts`、
  `approval-block.tsx`（计划编辑）、`chat-page.tsx`。
- 测试：`conversation-rewrite-api.test.ts`、`message-actions.test.tsx`、`chat-rewrite.test.tsx`、`chat-approval.test.tsx`。

### 8.3 代码高亮（packages/console-ui）

- `packages/console-ui/src/markdown-highlight.ts`（`languageFromClassName` / `normalizeLanguage` /
  `highlightCode`，单遍扫描 tokenizer）+ `markdown.tsx` 的 `code` 组件按语言渲染 token span；
  颜色只用既有令牌（`text-ha-accent-soft` / `text-ha-success` / `text-ha-faint` / `text-ha-warning`），不注入 raw HTML。
- 测试：`markdown-highlight.test.ts`、`markdown.test.tsx`（新增 2 例）。

### 8.4 错误边界（apps/web 壳）

- `apps/web/src/components/error-boundary.tsx`（class 组件，`componentDidCatch` → Sentry；fallback 提供重新加载与返回工作台），
  在 `apps/web/src/app.tsx` 包住 `<Routes>`；测试 `error-boundary.test.tsx`（3 例，mock Sentry）。

### 8.5 偏离

- 新增 404 码 `conversation.message_not_found`（契约未定义；跨账号会话仍 `conversation.not_found`）。
- `run_active` 的终态集合单独定义 `REWRITE_TERMINAL_RUN_STATUSES`（含 `cancelled`）；未复用不完整的历史常量。
- 422 用 `InvalidRewrite(AppError).status = 422`（沿用 `InvalidHookTarget` 先例）。
- 前端 `rewriteMessage(conversationId, messageId, handlers, content?)` 把 handlers 作为第 3 参（与 `streamMessage` 一致）；
  assistant 目标省略 `content` 时请求体为 `{}`，避免被 422。
- 计划编辑用可选 `onSavePlan` / `saveBusy` prop 向后兼容；保存仍走既有 `PATCH /api/v1/runs/{id}/plan`，计划状态保持 `awaiting_approval`。
