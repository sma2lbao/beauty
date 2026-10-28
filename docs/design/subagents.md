# 子代理委派:独立 context / 工具面 / 权限(P1-E)

> 状态:**已落地(2026-10-28)**。对应 `docs/gap-2026-10/08-refresh-2026-10-27.md` 的
> P1-E「子代理委派(独立 context/工具面/权限)」。本文是**子代理契约的唯一真源**:
> 数据模型、工具参数、生命周期、事件与过程项、API、用量与前端以本文为准;
> 过程项分类学本身见 [event-taxonomy.md](./event-taxonomy.md)。

## 1. 为什么

此前 `runs.parent_run_id` **只用于 retry**(`services/run_execution.py:311`):没有子
run 委派,也没有「子代理」这种一等角色。父 run 想拆活只能把活并进同一个对话与
context,结果是父上下文被细节淹没、长任务无法分治、成本无法按子任务归因。

主流做法(Claude Code subagents / Deep Agents sub-agents / Qwen Code SubAgents)
的差异化都在**隔离**:独立 context window + 自定义 system prompt + 特定工具子集

- 独立权限,父级只收摘要与产物引用。beauty-moon 已有 retry 的父子血缘、事件溯源
  与 P1-D 过程项分类学,缺的正是「委派」这一层。

## 2. 目标与非目标

**目标**

- 工具 `spawn_subagent`:run 内模型把子任务委派给一个**独立 context 的子 run**;
- 子 run 继承租户/工作区/MCP/审批策略,**不继承父对话上下文**;
- 可指定角色提示(`instructions`)与工具子集(只能收窄,不能放大);
- 父级只收「摘要 + 子 run 引用」,上下文只增摘要;
- 子 run 可取消、可超时;每个父 run 的并发/累计数量有上限;
- 父子树与各自成本可见(过程项 `kind=subagent` + 用量投影 + 前端面板);
- 与 P1-D 分类学一致:子 run 的事件与父级过程项落在同一 thread 下。

**非目标(本期不做)**

- per-run 模型选择已随 P1-G 落地(2026-10-28,见 [model-routing.md](./model-routing.md));子 run 本期沿用父 run 的模型,按子代理覆盖模型留后续;
- 子 run 异步 worker/并发编排(本期**内联执行**:工具调用期间阻塞父 run);
- 子代理注册表/命名代理模板(只支持调用时传 `agent_name` + `instructions`);
- AG-UI Subagents 事件族映射(适配层留待后续)。

## 3. 模型:run 树

`runs.parent_run_id` 语义从「重试血缘」扩展为**血缘**:

| 列              | 类型       | 含义                                                         |
| --------------- | ---------- | ------------------------------------------------------------ |
| `parent_run_id` | String(32) | 父 run;NULL = 顶层 run(用户直接发起)                         |
| `parent_kind`   | String(16) | `retry`(重试) / `subagent`(子代理委派) / NULL(旧数据,未知)   |
| `agent_name`    | String(80) | 子代理名称(仅 `parent_kind=subagent`;默认「子代理」)         |
| `agent_prompt`  | Text       | 子代理角色/边界/工作方式(仅 subagent;注入其系统提示)         |
| `agent_tools`   | JSON       | 子代理工具白名单(仅 subagent;`null`=继承父 run 的有效白名单) |

三级标识(P1-D 契约不变):`thread_id` = 会话 id,无会话时沿 `parent_run_id`
上溯根 run 得 `run:<root_run_id>`(子 run 与父 run 同 thread);`turn_id` = run.id。

## 4. 数据模型与迁移

- 新迁移 `migrations/versions/<rev>_run_subagents.py`,
  `down_revision = "b9e4d1f7c2a8"`(P1-D 的 run_todos);四列 `nullable` 追加,
  SQLite/PG 均支持,无回填、无数据损坏风险;
- `retry_run` 新建子 run 时补 `parent_kind="retry"`(旧 retry 行保持 NULL);
- `RunRead` 增 `parent_kind`、`agent_name`(默认 None,向后兼容);
- 子 run 不写 `skill_*` 快照(技能不继承)、`conversation_id=None`(对话不继承)、
  继承父 run 的 `workspace_id` 与 `mcp_server_ids`。

## 5. 工具契约 `spawn_subagent`

**暴露条件**(与 `todo_write` 同一挂载点:`core/container_assemblies.py` 的
extras 工厂)

1. 在 run 上下文执行(`current_tool_context().run_id` 非空);
2. 当前 run **不是**子代理(`parent_kind != "subagent"`);
3. `settings.subagent_enabled`(默认 True)。

**风险等级**:`WRITE`(自动执行,不进人工确认闸)。子 run 内部遇到危险工具时仍走
**租户级审批策略**——子 run 会停在 `awaiting_*`,由人工在其运行详情继续。

**参数**

| 参数              | 类型     | 必填 | 约束/默认                                                       |
| ----------------- | -------- | ---- | --------------------------------------------------------------- |
| `title`           | string   | 是   | 1..120 字,子任务短标题(UI 卡片与子 run 标题)                    |
| `task`            | string   | 是   | 1..`subagent_task_max_chars`(4000),子代理要完成的任务与验收标准 |
| `agent_name`      | string   | 否   | ≤40 字,默认「子代理」                                           |
| `instructions`    | string   | 否   | ≤4000 字,角色/边界/工作方式(注入子运行系统提示)                 |
| `tools`           | string[] | 否   | ≤`subagent_max_tools`(12);只收窄:必须是父级有效白名单的子集     |
| `timeout_seconds` | number   | 否   | 30..1800;默认 `subagent_timeout_seconds`(600)                   |

**返回**:单行 JSON(工具输出只含摘要,父 context 只增摘要)

```json
{"ok": true, "subagent_run_id": "...", "status": "completed", "summary": "……", "usage": {"total_tokens": 1234, "calls": 5}}
{"ok": true, "subagent_run_id": "...", "status": "awaiting_approval", "summary": "", "note": "子代理等待人工审批,可在运行详情继续"}
{"ok": false, "error": "subagent.limit_exceeded", "detail": "每个 run 最多 3 个子代理"}
```

**错误码**:`subagent.depth_exceeded`(超过 `subagent_max_depth`)、
`subagent.limit_exceeded`(该父 run 累计子代理数超限)、
`subagent.tools_not_subset`(请求工具越出父级白名单)、
`subagent.invalid_arguments`(长度/枚举/数值越界)。
错误作为普通工具结果回喂模型(不中断 run)。

**工具面校验**:父级有效白名单 = `run.agent_tools ?? run.skill_tools ?? null`;
为 `null`(不限制)时只做数量/长度校验;为列表时 `tools` 必须为其子集,
并剔除 `spawn_subagent` 自身(防递归)。

## 6. 生命周期

```
校验参数/上限
  → 建子 run(继承 tenant/workspace/mcp;conversation=None;agent_* 落列)
  → 落 run.created.v1(含 parent_run_id/parent_kind/agent_name)
  → 父级 item kind=subagent started(thread/turn 同父;item_id `<parent>:<attempt>:subagent:<n>`)
  → 内联执行子 run(同级 plan_run 语义,jobs_enabled=False;不排队)
  → 每 1s 轮询:子 run 终态 / 子 run 被取消 / 父 run 被取消 / 超时
  → 终态:取摘要(产物正文或 run.completed.v1.results,截断 subagent_summary_max_chars)
     + 汇总子 run 用量
  → 父级 item completed(或 failed)
  → 返回摘要 JSON
```

**状态映射**

| 子 run 终态                                   | item phase/status                 | 工具返回                     |
| --------------------------------------------- | --------------------------------- | ---------------------------- |
| `completed`                                   | `completed` / `completed`         | ok=true, status=completed    |
| `failed` / `blocked` / `cancelled`(超时/取消) | `failed` / `failed`,`data.reason` | ok=false 或 ok=true + status |
| `awaiting_approval`/`confirmation`/`input`    | `completed` / `completed`         | ok=true, status=`awaiting_*` |

**超时**:`effective = clamp(tool.timeout_seconds ?? setting, 30, 1800)`;到期取消子
任务并把子 run 落 `cancelled`(原因「子代理超时」),item 失败 `data.reason="timeout"`。

**取消**:子 run 被 `POST /runs/{id}/cancel` 取消 → 轮询发现即取消执行任务,
item 失败 `data.reason="cancelled"`;父 run 被取消 → 连带取消子 run(同一轮询)。

**待人工**:子 run 停在审批/确认/提问时**立即返回**,不阻塞父 run;子 run 在
`/runs` 与父级「子代理」面板可见,人工处理后可重试/续跑。

## 7. 事件与过程项(P1-D 分类学)

- **父级**只新增一类过程项:`run.item.v1` `kind="subagent"`;
  - `started` data:`child_run_id`、`agent_name`、`title`、`tools`、`timeout_seconds`;
  - `completed` data:`child_run_id`、`agent_name`、`child_status`、`summary`(截断)、
    `usage`(子 run 用量汇总)、失败时 `reason`;
  - `item_id = "<parent_run_id>:<attempt>:subagent:<n>"`,`n` 由父 run 已落库的
    `subagent` item 数续编;`thread_id`/`turn_id` 与父 run 相同;
  - `node` 取调用发生的节点语义(`execute`),`step_id` 取当前步骤(拿不到时为 null,
    与 plan 相位同一例外)。
- **子级**保留自身事件流:子 run 的 `run.created.v1` payload 增
  `parent_run_id`/`parent_kind`/`agent_name`;其 `run.item.v1`(plan/tool/…)、
  `run.step.v1`、`token.delta.v1` 都落在同一 thread、`turn_id=child_run_id`。
- 不新增父级 gate 事件:父子关系由 `runs.parent_run_id` + item `data.child_run_id`
  双向可达,回放可重建完整过程。
- `event-taxonomy.md` 同步:§4 增 `subagent` 行,§7 增发射点。

## 8. API

| 端点                              | 返回                          | 说明                                                           |
| --------------------------------- | ----------------------------- | -------------------------------------------------------------- |
| `GET /api/v1/runs/{id}/subagents` | `list[SubagentRead]`          | 子代理列表(按创建时间);先 `require_run`(跨租户 404);空数组合法 |
| `GET /api/v1/runs/{id}/usage`     | `RunUsageRead`                | run 用量汇总(含 `by_model`);无用量返回零值                     |
| `RunRead`                         | 增 `parent_kind`/`agent_name` | 看板徽标与子 run 头部                                          |

`SubagentRead`:`run_id`、`title`、`agent_name`、`status`、`status_reason`、
`parent_run_id`、`created_at`、`usage`(RunUsageRead 的汇总部分)、`summary`
(父级 item 里最近一次完成摘要,null=尚未结束)。

## 9. 用量与成本

- 新增事件 `run.usage.v1`(本地投影,`record_outbox=False`,与过程项同思路):
  在 `_usage_boundary` 退出时按本次采集器数据落一条,payload
  `{calls, prompt_tokens, completion_tokens, total_tokens, models: {"<model>": {...}}}`;
  无采样不落;
- 与 account `usage_events` **同源**(都来自同一 `UsageCollector`):account 侧保留
  跨服务计费/配额口径,本地事件用于 run 内自洽、回放与 UI 展示(含子代理归因);
  它随 `/events` 与 SSE 一起下发,但**不是过程项**:前端对未识别的事件类型直接忽略,
  过程项时间线只看 `run.item.v1`;
- `GET /runs/{id}/usage` 聚合该 run 的 `run.usage.v1`;子代理面板展示
  「父 run 自身 + 子代理合计」,每行给出子 run token 数。

## 10. 前端

- `apps/web/src/lib/runs-api.ts`:`Run.parent_kind?`/`agent_name?`、`Subagent`、
  `RunUsage`、`runsApi.subagents(runId)`、`runsApi.usage(runId)`;
- `query-keys`:`runSubagents`、`runUsage`;
- `run-detail.tsx` 新增「子代理」面板(有子 run 或 item 时显示):父 run 用量汇总 +
  子代理行(名称/标题/状态/用量/摘要可展开/「打开」→ `setSelectedRun(child_run_id)`
  就地切换抽屉;子 run 详情沿用既有「父 run」链接返回);
- `run-item-timeline.tsx` 新增 `kind=subagent` 卡片:名称、子 run 状态、成本、
  摘要(可展开)、`onOpenRun` 可选回调;
- `/runs` 看板卡片:`parent_kind === "subagent"` 显示「子代理」徽标;
- 空态/错误态与既有面板一致(1.5s 轮询、终态静默)。

## 11. 隔离 / 权限 / 安全

- **继承**:tenant、`workspace_id`、`mcp_server_ids`、租户级审批策略、LLM 路由;
- **不继承**:父对话(`conversation_id=None`)、父技能快照、父 pending 指令;
- **工具面**:只能收窄;`spawn_subagent` 不出现在子 run 的工具面
  (`subagent_max_depth` 默认 1,再叠加工具剔除);
- **上限**:每父 run 累计子代理 `subagent_max_per_run`(3);深度 1;
  `timeout_seconds` 上限 1800;摘要/任务/指令长度截断;
- 子 run 事件仍按既有策略记录(outbox 只收 run 级事件;过程项与用量是本地投影)。

## 12. 验收对照

| P1-E 验收项               | 本设计落点                                                             |
| ------------------------- | ---------------------------------------------------------------------- |
| 子任务在独立 context 完成 | 子 run 新 thread/turn、不继承对话;父级只收摘要 JSON                    |
| 父 context 只增摘要       | 工具输出=摘要(截断);父级只多一条 `kind=subagent` 过程项                |
| 子 run 可取消             | 子 run 走既有 `cancel`;spawn 轮询发现即停并落 item failed              |
| 子 run 可超时             | `subagent_timeout_seconds` + 参数覆盖;超时取消子 run                   |
| UI 可见父子树             | `/runs` 徽标 + run 详情「子代理」面板 + item 卡片 + 既有「父 run」链接 |
| UI 可见各自成本           | `GET /runs/{id}/usage` + 子代理行用量 + item `data.usage`              |
| 事件树可回放(P1-D 依赖)   | item `kind=subagent` + 子 run 事件流同 thread/turn 契约                |

## 13. 测试计划

- 后端 `tests/test_subagents.py`:参数校验(长度/枚举/越界)、深度与数量上限、
  工具子集校验、子 run 字段继承(workspace/mcp/不继承 conversation)、
  完整 spawn(completed/failed/awaiting)、超时取消、子 run 取消、
  父 run 取消连带、摘要截断、用量事件与 `GET /usage` 汇总、
  `GET /subagents` 列表与 summary、item `kind=subagent` 生命周期与 thread/turn;
- `tests/snapshots/openapi.json` 重生成(新端点 + RunRead 字段);
- 前端 `run-item-timeline.test.tsx`(subagent 卡片)、`run-detail.test.tsx`
  (子代理面板)、`runs-api.test.ts`(subagents/usage);
- 门禁:`npx nx lint/format agent-orchestrator`、`npx nx test agent-orchestrator`、
  `npx nx typecheck/lint/test @beauty/web`、`scripts/check-docs.sh`、
  `scripts/check-workspace.sh`。

## 14. 后续

- 子代理可选更便宜模型(per-run 选择已随 P1-G 落地;子代理当前沿用父 run 模型);
- 异步子 run / 并发编排(worker + 事件树实时增量);
- 命名子代理注册表(可复用的 agent 模板与权限包);
- AG-UI `Subagents` 事件族映射与跨端渲染。
