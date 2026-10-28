# Run 与 Step 模型对照：主流 agent 的 step 语义与 beauty-moon 的缺口（2026-10-25）

> 取证基线：`HEAD b3bc69d` + **工作树未提交改动**。P0-2（run 生命周期收尾）正在并行实现，已落地但未提交的改动见 §2.4；行号是取证时工作树，P0-2 提交后可能位移。
> 取证方法：外部事实用 web_search / web_fetch 取官方文档正文、官方仓库 README 与源码（官网 403 的用官方镜像，逐条标注）；代码逐文件 read + grep 复核，不采信既有文档的二手结论。来源编号 [S1]–[S26] 见 §5.2。
> 范围：只回答"run 是否缺 step / 缺什么 / 该怎么补"，以及它与 P0-1（成本归因）、P1-4（checkpoint 回退）、P0-2（状态机）的关系；不评审 P0-2 实现细节。
> 判定口径：✅ 已具备或与主流同档；⚠️ 部分具备 / 有壳无实；❌ 缺失。规模：S ≤ 2 天 / M 3–5 天 / L 1–2 周+。
> 与 06 的关系：`06-refresh-2026-10-25.md` 已判定"planning/running 有壳无实、无 cancel"（本工作树已被 P0-2 修掉大半，见 §2.4）；本篇不重复能力清单，只补 **step 维度**的对照与取证。

## 0 一页结论

**1. run 缺 step，但缺的不是"事件流"。** run_events 已经能回放"发生了什么"（append-only、每 run seq 单调、类型化），但答不了四个问题：**第几步 / 哪一步 / 这一步多久与多少 token / 能不能只重跑或回退这一步**。缺的是"可寻址的 step 身份 + step 级元数据 + step 视图"三件套，而不是再造一套事件表。

**2. 主流对 step 的"一等性"分两派，但都暴露可寻址条目。** 协议/运行时一派把 step 当一等对象：OpenAI Codex 的 Thread/Turn/Item 三层都持久化并带 id [S4][S5][S6]，OpenHands 的 Event 日志是记忆与集成点 [S7]，Google ADK 的 Event 是"信息流基本单位" [S16]，LangGraph 的 checkpoint 与 super-step 由运行时持久化 [S9][S10]。模型自报一派把 step 当投影：Claude Code 的 TodoWrite [S2]、LangChain Deep Agents 的 `state.todos` [S18]、OpenAI Agents SDK 的 `new_items` [S13] 都不是业务表，而是 tool call / state / 返回列表的投影。**没有任何一家把"每个 LLM 调用建一张 step 表"当成硬前提；但没有任何一家让 UI 直接面对无结构的原始事件流。** 差别在"最小可寻址条目"有没有被定义。

**3. 回退能力都由"快照"承载，而 beauty-moon 没有 step 锚点就接不上。** Claude Code /rewind 以 turn 为单位列出发过的 prompt，可 restore code / conversation / both [S1]；Codex 移除 thread/rollback 改用 thread/revert（分页线程级回退）[S5]；LangGraph time travel 支持从任意 checkpoint replay/fork，节点从该 super-step 重跑 [S11]。beauty-moon 的 retry 是**整 run 新建 child run**（`run_execution.py:201-262`），resume 只允许 blocked（`run_lifecycle.py:33`），checkpoint 的线程 id 只到"第几次尝试"（`run_execution_approvals.py:108`）。P1-4 要做 rewind，**第一件事就是补 step/seq ↔ checkpoint 的锚**。

**4. 成本归因（P0-1）卡在"没有维度键"。** 公开面看，主流成本是 session/model/组织维度聚合（Claude Code /usage 按会话与模型、本地估算美元 [S3]；Devin 按 ACU、组织/用户维度 [S24]），per-step 成本并不常见——但这恰恰是因为它们的 step 本身没有成本诉求。beauty-moon 若要做"阶段/节点/子代理成本"，`step_id` 是唯一可落地的外键：当前 usage 采集器只有 token（`services/usage.py:26-33`），上报体只有 `run_id/model/prompt/completion`（`:108-113`），account 的 `usage_events` 也只有 `run_id/model/token`（`services/account-service/account/models/account.py:75-84`），连 phase 都没有。

**5. 当前前端把"工具调用"当成了步骤。** `execution-panel.tsx:77-129` 直接 map `run.tool.called.v1` 渲染"执行步骤"；规划、审批、验收、交付这些节点没有出现在步骤里。事件里其实已有 `phase`（plan/execute/verify/deliver，`graph/delta.py:19-22`），但它只藏在 `token.delta.v1` 的 payload 里（`services/run_events.py:114`），不是 step 身份。P0-2 修好 planning/running 赋值后，看板 running 列不再恒空（`apps/web/src/pages/runs.tsx:81`、`apps/web/src/lib/presentation/run.ts:56-81`），但"步骤时间线"仍然是工具调用列表。

**6. 推荐选项 C（事件投影优先 + 最小元数据契约 + 按需物化）。** 第一步在 run_events payload 固化 `step_id/node/phase/attempt/started_at/finished_at/tokens`，并加只读 `GET /runs/{id}/steps` 做投影（**不动迁移**）；第二步给 `usage_events` 加 `step_id/phase/cost_usd` 支撑 P0-1；第三步用 `step_id ↔ (thread_id, attempt, checkpoint_id)` 映射支撑 P1-4；`run_steps` 表只在出现"SQL 统计 / 单步重试"需求时物化，且可由事件重建。起步成本 S，不与 P0-2 抢迁移。

## 一、主流对比矩阵（含来源列）

### 1.1 矩阵

| 对象                  | step 的命名                                                      | 一等实体还是投影                                                                      | 粒度                                                                                                     | 生命周期 / 持久化                                                                                                                                                 | 可单独 retry / rewind                                                                                                                     | 承载 token / 成本                                                                  | 前端怎么展示                                                                                | 来源                |
| --------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------- |
| Claude Code           | turn / checkpoint / TodoWrite 任务项                             | checkpoint 是持久化一等对象；**step 无独立实体**，todo 是 tool call 投影              | 一个 turn = 一次用户 prompt 触发的一回合；内部工具调用是连续流                                           | checkpoint 与对话一起保存，最近 100 个，默认约 30 天清理；resume 后仍可 /rewind                                                                                   | ✅ /rewind 列出本会话发过的 prompt，可 restore code+conversation / 仅 code / 仅 conversation / summarize from here / summarize up to here | ❌ turn 不承载；成本按会话聚合、按 model 分，本地估算美元                          | CLI 列 prompt 历史；TodoWrite 由宿主应用读 tool call 后渲染 checklist                       | [S1][S2][S3]        |
| OpenAI Codex CLI      | Thread / Turn / Item                                             | Thread、Turn、Item 都是 app-server 协议一等对象并持久化（JSONL rollout）              | Turn = 一轮交换；Item = turn 内原子项（命令执行、文件变更、MCP 调用、推理、计划、Web 搜索……），每项带 id | turn 状态 inProgress / completed / interrupted / failed；turn/interrupt 可打断；thread/start、thread/resume、thread/fork；thread/items/list 支持 item anchor 分页 | ⚠️ turn 可 interrupt；thread 级回退用 thread/revert（旧 thread/rollback 已移除）                                                          | 待核实：turn/item 是否带 usage（[S4][S5] 未见，标「待核实」）                      | TUI/客户端按 turn 与 item 渲染；app-server 提供 turns/items 列表接口                        | [S4][S5][S6][S23]   |
| OpenHands             | Event（Action / Observation / Message / SystemPrompt / Control） | Event 日志是**一等实体**：不可变、类型化、append-only，既是 agent 记忆也是集成点      | 每个 action / observation 一步（工具调用一步）；agent 回复是 MessageEvent                                | conversation events 持久化；Agent Canvas 是控制面（Browser UI / Backend / Workspace / Agent and model）                                                           | ⚠️ 事件不可变；是否支持 rewind / 回放（标「待核实」）                                                                                     | 待核实（SDK metrics 页未取证）                                                     | Agent Canvas / Web 按事件流展示，可点开单条事件与工具动作                                   | [S7][S8]            |
| LangGraph             | super-step / checkpoint / task                                   | checkpoint 是持久化一等实体；**step = super-step 是运行时概念**，业务无需自建 step 表 | 一个 super-step = 图的一轮推进；并行节点属同一步；checkpoint 只在 super-step 边界落盘                    | 每个 super-step 存图状态快照，按 thread 组织；tasks 流需要 checkpointer                                                                                           | ✅ time travel：从任意 checkpoint replay（重试）或 fork（改状态分叉），之后节点重跑                                                       | ❌ 图/checkpoint 不承载 token/成本；LangSmith 承载（标「待核实」）                 | Studio / stream modes（updates / tasks / debug）投影步骤；tasks 有 start/finish 与结果/错误 | [S9][S10][S11][S12] |
| OpenAI Agents SDK     | RunItem（`new_items`）/ trace span                               | **投影**：返回结果里的列表，不是持久实体；span 在 tracing 后端                        | 每项 LLM 输出 / 工具调用 / 工具输出 / handoff / 审批各一项                                               | RunResult 持有；Session 与 `to_state()` 支撑恢复；`raw_responses` 保留原始模型调用                                                                                | ⚠️ 无 step 级 retry/rewind API（标「待核实」）；靠重跑 + `to_input_list()` 重放                                                           | ✅ ModelResponse 带 usage；tracing 的 generation_span / function_span 记录输入输出 | 无内置 UI；traces 面板可视化 span；应用自己渲染 `new_items`                                 | [S13][S14][S15]     |
| Google ADK            | Event                                                            | **一等实体**：事件是 ADK 信息流的基本单位，`session.events` 持久化                    | 每次"显著发生"一步（用户消息、agent 回复、function call、function response、状态变更、控制信号、错误）   | 带 `id/timestamp/author/invocation_id/actions/branch/turnComplete/partial/longRunningToolIds`；invocation 聚合整次交互                                            | 待核实（事件文档无 rewind 字样）                                                                                                          | 待核实（事件页无 `usage_metadata` 字段）                                           | adk web 开发 UI 展示 event 流（标「待核实」）                                               | [S16]               |
| Qwen Code             | subagent / TodoWrite                                             | subagent 是独立上下文 agent（工具级一等）；进度与统计是执行器上报的投影               | 子代理任务（macro-step），一个子代理内部多轮                                                             | 默认前台、可 `run_in_background`；Codex 委派是 ephemeral thread，不能收消息/恢复；Claude Code 走 ACP，会话可续                                                    | 待核实（子代理可重跑任务）                                                                                                                | 可上报工具使用与执行统计；**Codex 委派明确不上报 token 与成本**                    | TUI 实时显示子代理进度、工具使用、执行统计                                                  | [S17]               |
| LangChain Deep Agents | todos / subagents                                                | **投影**：TodoListMiddleware 用 `write_todos` 工具维护 `state.todos`                  | 任务项级别，状态 pending → in_progress → completed                                                       | 随 graph state / checkpointer 持久化；无独立 step API                                                                                                             | 无 step 级；依赖底层 LangGraph checkpoint（待核实）                                                                                       | ❌ 无                                                                              | `useStream` 直接读 `stream.values.todos` 实时渲染 checklist                                 | [S18]               |
| Devin                 | steps / Progress tab                                             | 会话内展示一等：shell 命令、代码编辑、浏览器活动统一成 steps，可点开                  | 每个动作 / 命令一步                                                                                      | session 持久化；步骤有完整命令历史、output preview、时间导航                                                                                                      | ⚠️ 时间导航是"跳到会话某点"而非回退（是否可回退标「待核实」）                                                                             | ACU 计费，组织/用户维度（具体页标「待核实」）                                      | Progress tab 统一视图 + 时间导航，点击命令跳转                                              | [S19][S20][S24]     |
| Manus                 | task / message event / status_update                             | **task 是一等实体**；进度是消息/事件流的投影                                          | task 整体 + message/event 流（无独立 step）                                                              | `agent_status` running / stopped / waiting / error；stopped 不一定是终态（后台 job 可能唤醒）；可 sendMessage / confirmAction                                     | 无 step 级；重跑 = 新 task（待核实）                                                                                                      | credits 计费（待核实）                                                             | App 任务进度；API 侧 `task.listMessages` 轮询                                               | [S21][S22]          |

### 1.2 横向归纳（矩阵读出来的五条）

1. **"step 是一等实体"与"step 落在哪"是两个问题。** OpenHands / ADK 把事件本身当实体；LangGraph 把 **checkpoint** 当实体、step 是 super-step 的投影；Codex 把 Thread/Turn/Item 三层都持久化；Claude Code 只持久化 turn 级 snapshot + tool call 流。共同点不是"有 step 表"，而是**最小可寻址条目有 id、有顺序、有状态、能被 UI/协议单独引用**。
2. **粒度主流集中在 turn（回合）与 action/event（动作）两档，工具调用不是独立实体。** Claude Code / Codex 以 turn 为回退与展示单位 [S1][S4]；OpenHands / ADK 以单次 action/observation 为最小事件 [S7][S16]；LangGraph 是 super-step 而非单节点 [S9]。把"每个 tool call 建一行"没有先例。
3. **回退能力几乎都绑定快照，而不是绑定一张 step 业务表。** Claude Code = checkpoint（文件+对话）[S1]；LangGraph = checkpoint（图状态）[S10][S11]；Codex = thread/revert 的分页回退 [S5]。这说明 beauty-moon 想接 P1-4，先把 **事件 seq / step 身份 ↔ checkpoint** 打通比建表更关键。
4. **token/成本在 SDK 层比产品层细。** OpenAI Agents SDK 的 `ModelResponse.usage` 与 tracing span 能细到单次 LLM 调用 [S13][S14]；但产品层对外按 session/model/ACU 聚合 [S3][S24]。beauty-moon 的 P0-1 要做"阶段成本"，介于两者之间：比产品层细（按 phase/node），但不必到每次 model call。
5. **前端没有一家直接渲染"step 表"。** Devin 是 Progress + 时间导航 [S19]，OpenHands 是事件流 + Canvas [S7][S8]，LangGraph 是 tasks/stream 投影 [S12]，Deep Agents 是 `state.todos` 投影 [S18]。**前端要的是投影后的步骤序列，而不是后端表的镜像**——这正好支持 beauty-moon 走"事件投影 + 最小元数据"而不是先建表。

## 二、beauty-moon 现状（file:line）

### 2.1 数据模型：没有任何 step 维度

| 表 / 组件                  | 位置                                                                  | 关键列 / 行为                                                                                                                                                | step 相关判定                                                                                               |
| -------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| runs                       | `services/agent-orchestrator/agent_orchestrator/models/runs.py:23-66` | `status`（String32，默认 pending）、`rounds`、`attempts`、`plan_markdown`、`status_reason`、skill 快照列、`parent_run_id`、`conversation_id`、`workspace_id` | ⚠️ 只有 run 级状态与轮次；无 `current_step`、无进度、无 step 表                                             |
| run_events                 | `models/runs.py:69-85`                                                | `run_id`、`seq`、`type`、`payload`（JSON）、`occurred_at`；`UniqueConstraint("run_id","seq")`（:78）                                                         | ❌ 无 `step_id/node/phase/duration/token` 列；`type` 只是 String100，payload 无 step 契约                   |
| jobs                       | `models/runs.py:98-119`、`services/jobs.py:13`                        | `kind`（plan/approve/confirm/resume/retry）、`run_id`、`payload`、`status`、`attempts`、`lease_until`、`locked_by`                                           | ❌ job 粒度 = **run 动作**，不是 step；一个动作可跨多个图节点                                               |
| usage（orchestrator 采集） | `services/usage.py:26-33`                                             | `model`、`prompt_tokens`、`completion_tokens`、`total_tokens`                                                                                                | ❌ 无 `step_id/phase/cost`                                                                                  |
| usage_events（account）    | `services/account-service/account/models/account.py:75-84`            | `run_id`、`model`、`prompt_tokens`、`completion_tokens`、`total_tokens`                                                                                      | ❌ 无 step/phase/cost 维度；聚合只按 model（`services/account-service/account/services/insights.py:56-80`） |
| checkpointer               | `core/container.py:429-463`、`graph/builder.py:77`、`:122`            | 生产 PG 用 `AsyncPostgresSaver`，SQLite/dev 回退 `InMemorySaver`；线程 id = `f"{run.id}#{run.attempts}"`（`run_execution_approvals.py:108`）                 | ⚠️ checkpoint 以"一次尝试"为线程；**没有 step/seq 到 checkpoint 的索引**                                    |
| 迁移                       | `migrations/versions/1cef00be914c_runs_schema.py:55-68`               | run_events 建表列同上                                                                                                                                        | ❌ 迁移里没有 step 列，可直接印证"设计上从未有 step 实体"                                                   |

### 2.2 事件清单：能回放"发生什么"，回放不出"第几步、状态怎么迁移"

全仓 grep 到的持久化事件类型（`append_event` 的 type 实参）：

| 事件                                          | 触发处                                                                 | 语义 / 载荷要点                                                                          | 当 step 用的问题                                                                                              |
| --------------------------------------------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `run.created.v1`                              | `services/run_lifecycle.py:68-118`                                     | 建 run；载荷 title / skill_id                                                            | 只有 run 起点，无 step                                                                                        |
| `run.planned.v1`                              | `services/run_execution.py:131`、`:135-140`                            | 计划产出；载荷 attempt（自动放行时带 auto_approved）                                     | 可当"plan 步骤完成"的信号，但无起止、无 node                                                                  |
| `run.approved.v1`                             | `run_execution.py:141-143`、`run_execution_approvals.py:235`           | 审批通过；载荷 attempt / auto                                                            | 可当"approval 步骤"信号                                                                                       |
| `run.plan.edited.v1`                          | `run_execution.py:186-198`                                             | 人工改计划；载荷 chars                                                                   | 无 step                                                                                                       |
| `run.retry.requested.v1`                      | `run_execution.py:201-262`（事件 :245）                                | 重试；载荷 new_run_id / failures / reason                                                | **粒度 = 整 run**，非 step                                                                                    |
| `run.resumed.v1`                              | `run_execution.py:265-296`（事件 :283）                                | 从 blocked 恢复；载荷 attempt                                                            | 无 step                                                                                                       |
| `run.tool.called.v1`                          | `run_execution_approvals.py:140-143`                                   | 工具审计；载荷 = `_record` 的四字段 name/arguments/ok/detail（`graph/nodes.py:212-218`） | ⚠️ 最接近 step，但**无 id / 起止 / 耗时 / token**；按类型计数差量补发（:141）说明身份是"序号计数"而非 step id |
| `run.confirmation.required.v1`                | `run_execution_approvals.py:144-154`                                   | 危险写待确认；载荷 pending_writes                                                        | 可当"confirm 步骤"信号                                                                                        |
| `run.confirmation.received.v1`                | `run_execution_approvals.py:286-296`（事件 :292）                      | 人员确认；载荷 action / reason                                                           | 同上                                                                                                          |
| `run.rejected.v1` / `run.blocked.v1`          | `run_execution_approvals.py:344-357`                                   | 计划被拒 → blocked                                                                       | 无 step                                                                                                       |
| `run.failed.v1`                               | `services/run_events.py:164-185`、`run_execution_approvals.py:173-189` | 失败；载荷 stage / reason（verify 带 rounds）                                            | ⚠️ stage 是**字符串阶段**，不是 step 身份                                                                     |
| `run.completed.v1`                            | `run_execution_approvals.py:190-197`                                   | 完成；载荷 rounds / results                                                              | 无 step                                                                                                       |
| `run.delivered.v1`                            | `services/run_artifacts.py:184-205`                                    | 交付物；载荷 artifact_id / kind / chars / size_bytes / storage / artifacts               | 可当"deliver 步骤"信号                                                                                        |
| `run.instruction.queued.v1` / `.consumed.v1`  | `run_lifecycle.py:128-153` / `_consume_instructions`                   | 追加指令排队 / 消费                                                                      | 无 step                                                                                                       |
| `token.delta.v1`                              | `services/run_events.py:68-117`（flush :110-116）                      | 正文增量；载荷 `{text, phase}`（:114）                                                   | phase 是唯一阶段信息，但**依附于 token 流**，不是 step                                                        |
| `run.cancelled.v1`（P0-2 工作树新增，未提交） | `services/run_cancel.py:40-45`                                         | 取消；载荷 reason / cancelled_jobs                                                       | 无 step                                                                                                       |

关键反证：

- **没有持久化的 `run.status_changed.v1`**：同名事件只出现在通知 SSE 帧里（`api/v1/notifications.py:100-102`，模块 docstring 明确"不引入通知表，事件已在 run_events 可回放"）。通知的"状态变化"是轮询 diff 出来的 **瞬时帧**，不落 run_events。
- **没有 `run.started.v1`**：只在 `graph/delta.py:24` 的注释里作为命名示例出现，未实际发送。
- 因此，即使 P0-2 工作树已经给 `planning` / `running` 赋值（`run_execution.py:80`、`:145`；`run_execution_approvals.py:232`、`:287`），这些**状态迁移也不落事件**（`run_execution.py:78-79` 注释明说"不落事件——事件流保持 created → planned 的既有契约"）。结论：**run_events 回放不出状态时间线，更回放不出 step 时间线**。

### 2.3 执行图：step 的"候选粒度"已经存在，只是没被命名

- 图节点 8 个：`plan / approval_gate / execute / confirm_gate / tool_exec / verify / deliver / fail`（`graph/builder.py:79-97`），边与条件路由见 `:99-122`。
- execute 内部最多 `MAX_TOOL_ROUNDS = 5` 轮 function-calling（`graph/nodes.py:63`、循环 `:265`）；verify 最多 `MAX_REWORK_ROUNDS = 3` 轮返工（`graph/builder.py:38`、`:64`）；确认最多 `MAX_CONFIRM_CYCLES = 3`（`nodes.py:64`）。
- 工具审计记录只有四个字段：`name / arguments / ok / detail`（`graph/nodes.py:212-218`），**没有调用 id、时间、耗时、token、所属轮次**。
- 图状态只装业务结果：`results / tool_calls / rounds / confirm_cycles / artifact / verified / final_status …`（`graph/state.py:12-43`），**没有"节点执行记录"结构**。
- 阶段标签已存在：`PHASE_PLAN / PHASE_EXECUTE / PHASE_VERIFY / PHASE_DELIVER`（`graph/delta.py:19-22`），随 token 增量上报（`run_events.py:114`）。

也就是说：**执行图已经天然提供了 step 的边界（节点）与子步骤（LLM 轮 / tool call），缺的是把这个边界命名、带 id、带元数据地记录下来。**

### 2.4 状态机与取消（P0-2 工作树现状，未提交）

- 状态枚举 9 态：pending / planning / awaiting_approval / running / awaiting_confirmation / blocked / **cancelled（P0-2 新增）** / completed / failed（`schemas/runs.py:12-25`）。
- 迁移表以"动作 → 允许的源状态"为唯一事实源（`run_lifecycle.py:26-46`）；retry 只允许 failed（:32）、resume 只允许 blocked（:33）、cancel 允许全部非终态（:36-45）。
- 取消：`POST /runs/{id}/cancel`（`api/v1/run_actions.py:217-229`）→ `run_cancel.py:25-47`：状态落 cancelled、排队 job 置 cancelled（`repositories.py:303-320`）、发 `run.cancelled.v1`；running 中的图调用不强制打断，执行边界回来读库发现 cancelled 就收手（`run_events.py:58-65` 的 `is_cancelled`、`run_execution_approvals.py:137-139`）。
- planning/running 现已赋值（`run_execution.py:80`、`:145`；审批/确认路径 `run_execution_approvals.py:232`、`:287`），但**不落事件**（见 2.2 反证）——看板状态会修复，状态时间线仍然缺失。
- 中断超时：配置项 `approval_timeout_seconds / confirmation_timeout_seconds` 与查询 `list_stale_interrupts`（`core/config.py:65-66`、`repositories.py:106-140`）已加，sweeper 尚未接线（截至取证时）。

### 2.5 重试与回退：只有 run 级，没有 step 级

- retry：failed → 新建 **child run**（`run_execution.py:201-262`），继承 goal/acceptance/plan/parent_run_id/conversation/workspace，父 run 发 `run.retry.requested.v1{new_run_id,...}`（事件 :245）。
- resume：仅 blocked → pending，attempts+1（`run_execution.py:265-296`）。
- 图线程：每次 attempt 一个新 thread，`thread_id = f"{run.id}#{run.attempts}"`（`run_execution_approvals.py:108`）——**checkpoint 的粒度是"第几次尝试"，不是第几步**。
- checkpoint 的生产化已接（`core/container.py:429-463`：`AsyncPostgresSaver.from_conn_string` + `await saver.setup()`；dev 回退 `InMemorySaver`，`graph/builder.py:77`、`:122`），但**没有任何 checkpoint 列表/回退/fork API**（P1-4 的缺口，06 已判定）。P1-4 要做 rewind，必须先有"事件/步骤 ↔ checkpoint"的映射。

### 2.6 用量与成本：run/model 级粒度，无阶段维度

- 采集：每次图调用一个 `UsageCollector`，样本只有 token（`services/usage.py:26-33`）；plan 与 approve 各自独立计数（`check_budget` :175 的注释明确"跨段累计如需更强约束，可后续把已落库用量带回 state"）。
- 上报：逐条 POST account，body 五字段（`services/usage.py:108-113`）；**上报失败即停、只 warning**（`_failed` :136-151）。
- 存储与聚合：account 的 `usage_events`（`services/account-service/account/models/account.py:75-84`）；`GET /usage/summary` 只按 model 聚合（`services/account-service/account/services/insights.py:56-80`、`api/v1/insights.py:34-36`）。
- 前端：用量页只展示今日/本月 token、请求次数、按模型分布（`apps/web/src/pages/usage.tsx:2`、`:38-53`、`:93-108`）——**无 run 维度、无阶段、无金额**。

### 2.7 SSE 与前端：事件流已到位，步骤视图是工具调用投影

- 服务端：`GET /runs/{id}/events` 按 seq 升序回放（`api/v1/runs.py:110-117`）；`GET /runs/{id}/events/stream` 支持 `Last-Event-ID` 续传（`:155-172`）；终态集合含 completed/failed/cancelled（`services/run_stream.py:30-34`）；SSE 帧格式 `id/event/data`（`core/sse.py:41-49`）。
- 前端事件层：`runs-api.ts` 类型与端点（`apps/web/src/lib/runs-api.ts:13-44`、`:86-142`，**没有 cancel/steps/checkpoints 端点**）、SSE 解析与合并（`:148-182`）、断线续传（`:185-218`）；通用 SSE 客户端（`apps/web/src/lib/sse.ts:136-164`）。
- 看板：筛选与计数（`apps/web/src/pages/runs.tsx:32-36`、`:80-81`）、列定义与过滤（`lib/presentation/run.ts:56-81`、`runs.tsx:84-90`、`:200-237`）、running 才显示进度条（`runs.tsx:358`、`:388-392`）、状态行（`:416-448`）；详情抽屉非终态 2s 轮询（`components/console/run-detail.tsx:52-56`）。
- 步骤面板：三 tab `steps/changes/terminal`（`execution-panel.tsx:24`），"执行步骤"= `toolCalls.map`（`:67-129`），`done` = ok 的 tool call 数（:48），底部"已执行 N 步 · 进行中"（`:244-249`）、`StatusStep` 只显示 rounds（`:256-322`）；工具卡（`tool-call-card.tsx`）、原始事件列表（`components/runs/run-events.tsx`）都是对同一批 `run.tool.called.v1` / 事件流的投影。
- 事件播报：`liveRunText` 只认 `token.delta.v1` 且 phase 一变就丢上一段（`lib/presentation/events.ts:18-33`）；`eventLine` 翻译 14 类事件（`:36-99`），**run.tool.called.v1 不在播报里**（走工具卡）。
- 通知：通知 SSE 只推 5 个状态（`api/v1/notifications.py:32-40`），首见 run 不通知、按 runId+status 去重（`lib/run-status-watcher.ts:1-12`、`:36-50`、`:144-151`），指数退避重连（:188-193）。

### 2.8 缺口小结

| 能力维度            | 主流基线                                                                    | beauty-moon 现状                                                                             | 判定                      |
| ------------------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------- |
| step 身份           | Codex `TurnItem.id`、OpenHands/ADK `event.id`、LangGraph task/checkpoint id | 只有事件自增 `(run_id, seq)`（`models/runs.py:69-85`），无 step 锚                           | ❌                        |
| 顺序与进度          | turn/item 顺序、super-step 顺序、事件顺序                                   | seq 有全序；"步骤"由前端按 tool call 自行拼                                                  | ⚠️                        |
| 起止 / 耗时         | Devin 时间导航、LangGraph tasks start/finish、ADK timestamp                 | `occurred_at` 单点（`:85`），无 started/finished/duration                                    | ❌                        |
| step 状态与失败归因 | turn 状态机、node 错误、action 状态                                         | 只有工具调用的 `ok` 与 run 级 `status_reason`（`nodes.py:212-218`、`run_events.py:164-185`） | ⚠️                        |
| token / 成本        | ModelResponse.usage、Claude /usage、Devin ACU                               | 采集/存储/聚合都只到 run+model（§2.6）                                                       | ⚠️（run 级有，step 级无） |
| 可寻址回退          | Claude /rewind、Codex thread/revert、LangGraph fork                         | retry=新 child run、resume=仅 blocked、checkpoint 无 API（§2.5）                             | ❌                        |
| 前端步骤视图        | Devin Progress、OpenHands Canvas、LangGraph tasks                           | 工具调用列表 + 事件播报（§2.7）                                                              | ⚠️                        |

## 三、判断：run 是否缺 step、缺什么、什么已有

### 3.1 结论

**缺。** 但要精确表述为三缺一不缺：

- **不缺"事件"**：append-only + seq 单调 + 类型化 + 可回放 + SSE 续传 + outbox 已齐，事件溯源的地基是好的。
- **缺"身份"**：事件没有 step 锚（`step_id` / `node` / `attempt` / `round`），无法把"多条事件"归并成一个可寻址的步骤，也无法把"一个步骤"拆成多条事件回放。
- **缺"元数据"**：没有起止时间、耗时、step 级 token/成本、step 状态（pending/running/ok/failed/skipped）。
- **缺"视图 + 操作面"**：没有 `GET /runs/{id}/steps`、没有步骤级重试/回退、前端没有步骤时间线（只有工具调用列表）。

一句话：**run_events 回答了"发生了什么"，但回答不了"第几步、哪一步、多久、多少 token、能不能只重跑它"。**

### 3.2 缺什么（逐项 file:line + 后果）

1. **❌ 无 step 身份**：`run_events` 五列无 `step_id`（`models/runs.py:69-85`）；`append_event` 只写 `type/payload/occurred_at`（`services/run_events.py:25-55`）。后果：前端与审计无法引用"第 3 步"，只能引用"第 7 条事件"；跨 run 统计"plan 步骤耗时"无从下手。
2. **❌ 无起止 / 耗时**：事件只有单点 `occurred_at`。后果：无法回答"规划花了多久""工具执行最慢的是哪一步"；metrics 只有结果计数（`metrics.py:18-37`：run_outcomes_total / job_failures_total / llm_upstream_errors_total）。
3. **❌ 无 step 级 token / 成本**：采集器只有 token（`services/usage.py:26-33`）、上报体只有 run/model/token（`:108-113`）、account 存储只有 run/model/token（`account/models/account.py:75-84`）、聚合只按 model（`insights.py:56-80`）。后果：P0-1 只能做"run 总成本"，做不了"阶段成本 / 节点成本 / 子代理成本"。
4. **❌ 无 step 生命周期**：状态只有 run 级（`schemas/runs.py:12-25`）；工具调用只有 `ok/detail`（`nodes.py:212-218`）。后果：无法区分"跳过（skipped）""失败但已重试""取消时正在跑"，也就无法做步骤级 SLO。
5. **❌ 无 step 级 retry / rewind**：retry 是新 child run（`run_execution.py:201-262`）、resume 仅 blocked（`:265-296`）、thread 只到 attempt（`run_execution_approvals.py:108`）、checkpoint 无 API（`core/container.py:429-463` 只装配）。后果：一次失败要重跑整个 run；P1-4 的"从某步回退"没有锚点。
6. **❌ 无 step 视图**：前端把 `run.tool.called.v1` 当步骤（`execution-panel.tsx:77-129`），漏掉 plan/approval/confirm/verify/deliver；播报只覆盖事件（`events.ts:36-99`）。后果：用户看到的是"工具流水"，不是"任务步骤"；planning/running 修好后看板列会活，但详情抽屉的"步骤"仍然只是工具卡。

### 3.3 什么已有（可直接复用，别重造）

- ✅ **事件溯源底座**：append-only、`(run_id, seq)` 唯一、seq 单调（`repositories.py:452`）、类型信封版本化（`run_events.py:38-47`）、outbox 事务投递（`:50-54`）。
- ✅ **生命周期事件已覆盖大部分节点**：created/planned/approved/tool.called/confirmation.required/received/rejected/blocked/failed/completed/delivered/cancelled（清单见 §2.2）——**映射成 step 不需要新增事件族，只需要 payload 补字段**。
- ✅ **SSE 回放能力**：按 seq 续传（`api/v1/runs.py:110-117`、`:155-172`、`run_stream.py:30-79`），步骤投影可以直接复用现有流，无需新通道。
- ✅ **图节点 + checkpointer**：节点边界清晰（`graph/builder.py:79-97`），生产 PG checkpointer 已接（`core/container.py:429-463`）；**step ↔ checkpoint 的映射有物理基础**。
- ✅ **工具审计与轮次**：`tool_calls` 四字段 + `rounds/attempts`（`nodes.py:212-218`、`run_execution_approvals.py:172`）可作为 step 的子项与计数。
- ✅ **状态机守卫**：迁移表是唯一事实源（`run_lifecycle.py:26-66`），P0-2 后含 cancel；step 状态机可仿此声明式实现。

### 3.4 反例与边界：什么时候"不算 step 实体也能用"

| 反例                  | 做法                                                      | 为什么不建 step 表也够                                                             | 什么情况下会失效                                            |
| --------------------- | --------------------------------------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Claude Code           | turn 级 checkpoint + TodoWrite tool call 投影（[S1][S2]） | /rewind 与进度展示都只需要 turn 序列 + tool call；todo 状态由宿主读 tool call 渲染 | 要做跨会话步骤统计 / 单步重试 / 成本归因时                  |
| LangChain Deep Agents | `state.todos` 数组投影（[S18]）                           | 前端 `useStream` 直接渲染 checklist；状态随图 state 走                             | 要 SQL 查询步骤历史 / 多 agent 归因时                       |
| OpenAI Agents SDK     | `new_items` 列表 + tracing span（[S13][S14]）             | 应用拿到结构化 item 足够渲染与重放；token 在 `ModelResponse.usage`                 | 要跨进程、跨 run 的步骤视图与成本归因时                     |
| LangGraph             | step = super-step，运行时投影（[S9][S12]）                | tasks/updates 流足够做步骤 UI；回退靠 checkpoint                                   | 业务要按"步骤"计费/统计/重试时（需把 task 映射成业务 step） |

**边界条件**（出现任意一条，就需要 step 身份，纯事件投影不够）：

1. 成本归因到阶段 / 节点 / 子代理（P0-1 的目标）；
2. 指定步骤回退 / fork / 单步重试（P1-4 的目标）；
3. 跨 run 的步骤成功率、耗时、SLO 统计；
4. 多 agent / 子代理（Qwen Code 式）需要按子任务归因；
5. 前端要展示"规划 → 审批 → 执行第 N 轮 → 验收 → 交付"的**任务步骤**而非工具流水。

反过来，**只做"最近在干什么"播报、进度条、事件时间线**时，现有事件投影完全够用——06 与当前前端已经证明这一点。beauty-moon 正从"够用"走向"要归因/要回退"，所以现在需要补最小 step 契约，而不是一步到位建大表。

### 3.5 粒度建议：step 取"图节点的一次执行"，tool call 是子项

- **step = 图节点的一次执行**，唯一键 `{run_id}:{attempt}:{node}:{round}`（round 用于 execute/verify 的循环；非循环节点取 1）。理由：图节点是现有架构里最稳定的语义边界，8 个节点覆盖 plan→approval→execute→confirm→verify→deliver→fail 全流程；与 LangGraph super-step 边界天然对齐（checkpoint 就落在节点边界）。
- **子项 = LLM turn 与 tool call**：execute 的 5 轮 function-calling（`nodes.py:63`）每轮一个子步骤；tool call 挂在子步骤下，不作为顶层 step（与 Codex Item、Claude Code tool call 同构）。
- **phase 复用现有值**：plan/execute/verify/deliver（`graph/delta.py:19-22`），不新造词表；`token.delta.v1` 的 `phase` 可直接作为步骤阶段标签来源。
- **step 状态**：pending / running / ok / failed / skipped / cancelled（失败重试时保留 failed 记录，step 级重试用新记录，不覆盖）。
- **事件 → step 映射**（不需要新事件族，payload 补字段即可）：

| 节点          | 现有事件（补 step 元数据）                              | step 起止来源                                 |
| ------------- | ------------------------------------------------------- | --------------------------------------------- |
| plan          | `run.created.v1` → `run.planned.v1` / interrupt         | created 后进图 → planned 或 awaiting_approval |
| approval_gate | `run.approved.v1` / `run.rejected.v1`                   | 计划产出 → 审批动作                           |
| execute       | `run.tool.called.v1`（子项）+ `token.delta.v1`（phase） | 每轮 LLM 起 → 轮止                            |
| confirm_gate  | `run.confirmation.required.v1` / `.received.v1`         | 需确认 → 确认动作                             |
| verify        | `run.failed.v1`（stage=verify）/ `run.completed.v1`     | 执行完 → 验收结论                             |
| deliver       | `run.delivered.v1`                                      | 验收通过 → 交付登记                           |

- **建议 payload 字段**（加在相关事件的 payload 内，向后兼容；不改表）：`step_id`、`node`、`phase`、`attempt`、`round`、`status`、`started_at`、`finished_at`、`duration_ms`、`tokens`（可选，与 usage 对账）、`parent_step_id`（子步骤/未来子代理）、`checkpoint_id`（P1-4 用，可选）。

## 四、选项 A/B/C 与推荐

### 4.1 三个选项

| 维度          | A：events-only 投影                                       | B：一等 `run_steps` 表                   | C：混合（推荐）                                           |
| ------------- | --------------------------------------------------------- | ---------------------------------------- | --------------------------------------------------------- |
| 新表 / 迁移   | 无                                                        | 1 张表 + 迁移 + 回填                     | 无（S1）；usage 加列在 account 侧（S2）；可选物化（S3）   |
| 事件契约      | **必须**加 step 字段（否则投影无身份）                    | 可选（可从表回填事件）                   | **必须**加（同 A）                                        |
| API           | `GET /runs/{id}/steps`（内存投影）                        | `GET steps` + 单步 retry                 | `GET steps`（投影）+ rewind / resume-from-step            |
| 前端          | 步骤时间线（读投影）                                      | 时间线 + 单步操作                        | 时间线 +（P1-4）回退                                      |
| P0-1 成本     | 弱：事件有 step 可粗归因，但 usage 表无维度、跨服务聚合难 | 强                                       | 强：`usage_events` 加 `step_id/phase/cost_usd`            |
| P1-4 回退     | 弱：无 `step ↔ checkpoint` 映射时只能线程级              | 强：表里存 `checkpoint_id`               | 强：`step_id ↔ (thread_id, attempt, checkpoint_id)` 映射 |
| 与 P0-2 关系  | 可并行，不改表                                            | **冲突风险**：同改状态机与迁移           | 可并行，S1 无迁移；S2 只动 account 侧                     |
| 双写 / 一致性 | 无                                                        | 事件 + 表双写，需对齐顺序与失败回滚      | S1 无双写；S3 物化表可由事件重建                          |
| 成本量级      | S（约 1–2 天）                                            | M–L（1–2 周+迁移/回填/并发语义）         | S → M → L 分三段                                          |
| 风险          | 事件多时投影查询变慢（可加索引 / 物化）                   | 迁移与"节点重跑"语义复杂，易变成第二真源 | 需守住"run_events 是唯一事件源"，物化表只读               |

### 4.2 推荐 C，理由

1. **与现有架构同向**：run_events 已是事件源真源；通知模块明确"不引入通知表"（`api/v1/notifications.py:4-11`）——step 用投影是同一设计哲学。
2. **P0-2 已在收敛状态机，不该再叠一张表**：迁移会与 P0-2 的 cancel/状态改动抢文件、抢评审；S1 只动 payload 与只读 API，零迁移。
3. **P0-1 必须要 `step_id` 才能做阶段成本**：在事件 payload 里定契约，等于先给成本归因准备好维度键；usage 表加列是第二步，顺序不能反（先加成本列再补 step 会把上报契约返工两次）。
4. **P1-4 的 rewind 需要"锚"而不是"表"**：LangGraph checkpoint 以 super-step 为边界，正好对应 step；把 `step_id ↔ checkpoint_id` 映射存进事件/轻量表即可支持 fork/rewind，不必先有完整 step 事务表。
5. **保留物化出口**：真出现"跨 run 步骤统计 / 单步重试"需求时，`run_steps` 可以由事件重建（可物化投影），不必现在承担迁移与双写风险。

### 4.3 与 P0-1 / P1-4 / P0-2 的关系

- **P0-2（状态机收尾）**：本篇不阻塞 P0-2。P0-2 修的是"状态真实 + cancel"；step 修的是"步骤可寻址"。**接口点是事件 payload**：P0-2 只在 DB 改状态、不落事件（`run_execution.py:78-79`），会导致提交后状态时间线仍不可回放；建议 P0-2 收尾时顺手在 `run.planned.v1 / run.approved.v1 / run.failed.v1` 的 payload 里带 `node/phase/attempt`（一行改动，不改表），step 投影即可复用。
- **P0-1（成本归因）**：依赖顺序 = **step 契约 → usage 维度 → 成本视图**。`usage_events` 加 `step_id/phase/cost_usd`（account 侧迁移）后，`insights.py` 聚合才能按阶段/节点出数；上报体（`services/usage.py:108-113`）需要同步扩字段，失败重试/本地缓冲另属 P0-1 自身课题。
- **P1-4（checkpoint 回退）**：依赖顺序 = **step 锚 → checkpoint 索引 API → rewind/resume-from-step**。建议：`GET /runs/{id}/checkpoints` 用事件 seq 做索引，`POST /runs/{id}/rewind` fork 新 thread + 新 child run（复用 `retry_run` 的 child 语义），新增 `run.rewound.v1` 事件记录来源 step/checkpoint。langgraph checkpoint id 与 step 的对应可在 S3 用"节点执行前后各记一次"验证。
- **反顺序风险**：先建 `run_steps` 再做成本 → 双写返工；先做成本再补 step → 上报契约返工两次；先做 rewind 再补 step → 回退粒度只能到 attempt。

### 4.4 落地顺序与验收（建议）

| 阶段  | 内容                                                                                                                        | 规模        | 验收标准                                                                                                                 |
| ----- | --------------------------------------------------------------------------------------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------ |
| S1 ✅ | 事件 payload 契约（step 字段）+ `GET /runs/{id}/steps` 内存投影 + 前端步骤时间线读投影                                      | S（1–2 天） | 一个含"规划→审批→执行 2 轮→验收→交付"的 run 能渲染 ≥5 个 step，每个 step 有 node/phase/status/起止时间；不改表、不改迁移 |
| S2    | usage 采集与上报带 `step_id/phase`；`usage_events` 加 `step_id/phase/cost_usd`（account 迁移）；summary 增加 run/phase 维度 | M（3–5 天） | `/usage/summary` 可返回指定 run 的阶段成本；上报失败可重试（P0-1 一并做）                                                |
| S3    | `GET /runs/{id}/checkpoints` + `POST /runs/{id}/rewind` + `resume-from-step`；可选物化 `run_steps`（事件重建）              | L（1–2 周） | 能从第 N 步 fork 出新 run，原 run 事件不变；checkpoint 列表可按 step 定位                                                |

S1 可与 P0-2 收尾同批（只动事件 payload 与只读 API）；S2 紧跟 P0-1；S3 即 P1-4 本体。**不在 P0-2 里做表迁移。**

**S2 锚点（P1-D 落地后）**：`run.item.v1` 的 `step_id` 即 S2 的成本/归因锚点——usage 采集与上报按 `step_id` 聚合即可，不需要新增 item 事件（见 [event-taxonomy.md](../design/event-taxonomy.md) §9）。

**S1 落地记录（2026-10-25）**：✅ 已实现——`graph/steps.py`（`StepProjector` + 用 `instrument_node` 包 5 个异步节点）、`services/run_events.py`（`_step_stream` 在 3 个 `ainvoke` 边界 flush，`_flush_steps` 生成 `step_id`）、事件 `run.step.v1`（payload 同本表约定，`record_outbox=False`）、只读 `GET /api/v1/runs/{id}/steps`（`services/run_steps.py`）、前端 `RunStepTimeline`（会话执行步骤 tab 与 run 详情抽屉）；测试 `tests/test_run_steps.py` 4 项（全生命周期 5 步、reject→resume 的 attempt=2、失败节点事件先于 `run.failed.v1`、租户隔离）+ `tests/test_sse.py` 按「seq 单调不连续」调整。零迁移。已知边界：步骤在 `ainvoke` 返回后才落库、可见事件 seq 有空洞、取消时该次 invoke 的步骤仍落库。**未做**：§4.3 提到的 P0-2 收尾「终态事件 payload 带 `node/phase/attempt`」仍为可选。

### 4.5 不建议做的事

- ❌ 不要为 `token.delta.v1` 建 step 或 step 子表：它是展示流，不是语义步骤。
- ❌ 不要把每个 tool call 提升为 DB 行：一个 LLM turn 可含多个 tool call（`nodes.py:265-318` 批量产生），硬拆会破坏"一次模型决策"的语义，也与主流（Item/Action 作为 turn 内项）不一致。
- ❌ 不要建"第二事件源"（如通知表式 step 状态表）：会与 run_events 争真源；物化表必须声明"可丢弃、可重建"。
- ❌ 不要在 P0-2 的工作树未提交时叠迁移：等 P0-2 合并后再开 S2 的 account 迁移。

## 五、未确认项与来源清单

### 5.1 未确认项

| #   | 问题                                                               | 影响                        | 核实方式                                                                       |
| --- | ------------------------------------------------------------------ | --------------------------- | ------------------------------------------------------------------------------ |
| U1  | Codex 的 turn/item 是否携带 token usage                            | 决定"turn 是否承载成本"一行 | 读 codex app-server 协议 schema 与 `items.rs` 的 usage 字段（[S5][S6] 未覆盖） |
| U2  | OpenHands 是否支持 rewind / 远程回放                               | 影响"事件可否回退"对比      | 读 OpenHands SDK events 页正文（当前只取到导航）与源码                         |
| U3  | ADK 事件是否带 `usage_metadata`、是否有 rewind                     | 影响 ADK 成本与回退判断     | 读 `google/adk-docs` 源码与 `LlmResponse` 定义                                 |
| U4  | OpenAI Agents SDK tracing span 是否含 usage/token 字段             | 影响"SDK 层成本粒度"判断    | 读 tracing 源码 / `generation_span` schema                                     |
| U5  | Devin/Manus 是否有 step 级 API/计费                                | 影响"产品层 step 粒度"判断  | 读产品文档 API 参考（当前只取证用户面 Progress/messages）                      |
| U6  | Claude Agent SDK 是否有 `total_cost_usd` / usage 字段              | 影响 per-run 成本基线       | 读 Agent SDK result message 文档（[S25] 未展开）                               |
| U7  | LangGraph checkpoint 与 run_events seq 的一一映射                  | 影响 S3 的 rewind 设计      | 在真实 PG 上跑一个 run，对比 super-step 边界与事件 seq                         |
| U8  | P0-2 工作树尚未提交（sweeper 未接线、jobs_worker 未传 `from_job`） | 本文行号会漂移              | P0-2 合并后复核 §2.4 / §2.5 行号与行为                                         |

### 5.2 来源清单

| 编号 | 内容                                                                                                  | URL                                                                                                                              | 强度 / 备注                                                                                                 |
| ---- | ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| S1   | Claude Code checkpointing（turn 级快照、/rewind）                                                     | https://code.claude.com/docs/en/checkpointing                                                                                    | 官方文档正文 ✅                                                                                             |
| S2   | Claude Code Agent SDK 任务追踪（TodoWrite 是 tool call）                                              | https://code.claude.com/docs/en/agent-sdk/todo-tracking                                                                          | 官方文档正文 ✅                                                                                             |
| S3   | Claude Code 成本（/usage、Session block、按 model、本地估算美元）                                     | https://code.claude.com/docs/en/costs                                                                                            | 官方文档正文 ✅                                                                                             |
| S4   | OpenAI Codex 核心概念（Threads / Turns / Items）                                                      | https://developers.openai.com/codex/concepts/overview                                                                            | 官方 URL 本机 403；正文取自官方镜像 https://mintlify.wiki/openai/codex/concepts/overview ⚠️                 |
| S5   | Codex app-server 协议 README（thread/start、resume、fork、turn/interrupt、thread/revert、items 分页） | https://raw.githubusercontent.com/openai/codex/main/codex-rs/app-server/README.md                                                | 官方仓库源码 ✅                                                                                             |
| S6   | Codex `TurnItem` 枚举与 item id                                                                       | https://raw.githubusercontent.com/openai/codex/main/codex-rs/protocol/src/items.rs                                               | 官方仓库源码 ✅                                                                                             |
| S7   | OpenHands SDK Event 系统（append-only 类型化事件日志）                                                | https://docs.openhands.dev/sdk/arch/events                                                                                       | 官方文档 ✅（正文抓取只拿到导航，事件类型事实来自此前完整抓取）                                             |
| S8   | OpenHands Agent Canvas 概览（开源控制面）                                                             | https://docs.openhands.dev/openhands/usage/agent-canvas/overview                                                                 | 官方文档正文 ✅                                                                                             |
| S9   | LangGraph Graph API（Pregel super-step 语义）                                                         | https://docs.langchain.com/oss/python/langgraph/graph-api.md                                                                     | 官方文档正文 ✅                                                                                             |
| S10  | LangGraph Persistence / Checkpointers（super-step 边界快照）                                          | https://docs.langchain.com/oss/python/langgraph/persistence.md、https://docs.langchain.com/oss/python/langgraph/checkpointers.md | 官方文档正文 ✅                                                                                             |
| S11  | LangGraph Time travel（replay / fork）                                                                | https://docs.langchain.com/oss/python/langgraph/use-time-travel.md                                                               | 官方文档正文 ✅                                                                                             |
| S12  | LangGraph Streaming（updates/tasks/debug 模式）                                                       | https://docs.langchain.com/oss/python/langgraph/streaming.md                                                                     | 官方文档正文 ✅                                                                                             |
| S13  | OpenAI Agents SDK Results（RunResult / new_items / to_state）                                         | https://openai.github.io/openai-agents-python/results/                                                                           | 官方文档正文 ✅                                                                                             |
| S14  | OpenAI Agents SDK Tracing（span 类型）                                                                | https://openai.github.io/openai-agents-python/tracing/                                                                           | 官方文档正文 ✅                                                                                             |
| S15  | OpenAI Agents SDK RunItem 变体清单                                                                    | https://openai.github.io/openai-agents-python/ref/items/                                                                         | 官方文档正文 ✅                                                                                             |
| S16  | Google ADK Events（"fundamental units of information flow"、字段与事件类型）                          | https://adk.dev/events/                                                                                                          | 官方文档 ✅；正文原文取自 https://raw.githubusercontent.com/google/adk-docs/main/docs/events/index.md       |
| S17  | Qwen Code Subagents（独立上下文、Codex 委派不上报 token/成本）                                        | https://qwenlm.github.io/qwen-code-docs/en/users/features/sub-agents/                                                            | 官方文档 ✅；原文 https://raw.githubusercontent.com/QwenLM/qwen-code/main/docs/users/features/sub-agents.md |
| S18  | LangChain Deep Agents Todo list（state 投影、write_todos）                                            | https://docs.langchain.com/oss/python/deepagents/frontend/todo-list.md                                                           | 官方文档正文 ✅                                                                                             |
| S19  | Devin Session Tools / Progress（steps 统一视图、时间导航）                                            | https://docs.devin.ai/work-with-devin/devin-session-tools                                                                        | 官方文档正文 ✅                                                                                             |
| S20  | Devin 首会话（Ask / Agent 模式）                                                                      | https://docs.devin.ai/get-started/first-run                                                                                      | 官方文档正文 ✅                                                                                             |
| S21  | Manus task.create（异步 task + 轮询）                                                                 | https://open.manus.im/docs/v2/task.create                                                                                        | 官方文档正文 ✅                                                                                             |
| S22  | Manus task lifecycle（agent_status、waiting/stopped 语义）                                            | https://open.manus.im/docs/v2/task-lifecycle                                                                                     | 官方文档正文 ✅                                                                                             |
| S23  | Codex issue：thread/items/list 与 thread/turns/list                                                   | https://github.com/openai/codex/issues/46316                                                                                     | 官方仓库 issue ✅（仅作接口命名佐证）                                                                       |
| S24  | Devin 文档索引（ACU 计费、usage policies 页面索引）                                                   | https://docs.devin.ai/llms.txt                                                                                                   | 官方索引 ⚠️（具体页标「待核实」）                                                                           |
| S25  | Claude Agent SDK overview（Sessions 可 resume/fork）                                                  | https://code.claude.com/docs/en/agent-sdk/overview.md                                                                            | 官方文档正文 ✅                                                                                             |
| S26  | DSH/仓库内既有取证基线：06 复核文档                                                                   | `docs/gap-2026-10/06-refresh-2026-10-25.md`                                                                                      | 仓库内文档（未提交）⚠️ 仅作上下文，不当作外部事实                                                           |

> 说明：[S4] 因本机访问官方 URL 403，改取同内容镜像；其余外部条目均取官方文档正文或官方仓库源码，未核实项已在 §5.1 标注。仓库内 file:line 均可按本文路径直接 grep 复核。
