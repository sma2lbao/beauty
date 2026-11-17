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
- 子 run 继承账号/工作区/MCP/审批策略,**不继承父对话上下文**;
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
**账号级审批策略**——子 run 会停在 `awaiting_*`,由人工在其运行详情继续。

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

两种模式:`jobs_enabled=False`(测试/进程内)走下面的内联执行;`jobs_enabled=True` 走 §6.1 的
异步等待。

**内联模式**

```
校验参数/上限
  → 建子 run(继承 account/workspace/mcp;conversation=None;agent_* 落列)
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

## 6.1 异步等待模式(L2,2026-11-17)

`jobs_enabled=True`(生产形态)时,spawn 不再内联跑子 run:子 run 的 `plan` 入 job 队列后
spawn 立刻返回 ack,父 run 在 `subagent_wait` 节点挂起;子 run 终态时由事件唤醒父 run 继续。

**为什么必须「先挂起」**:`jobs_worker.start_worker` 只起**一个**消费者任务;若父 run 仍占着
worker 等子 run,子 run 的 `plan` job 永远没人取 → 自锁。因此队列化、等待节点、唤醒三件事
必须同时落地,不能只做第一步。

**流程**

```
spawn(jobs_enabled) → 子 run 落 run.created.v1 → 入队 plan job(dedupe=False) → 返回 ack
execute 节点识别 ack(pending=true + subagent_run_id)→ 收集 pending_subagents
批次结束 → route_after_execute 走 subagent_wait;若同回合还有待确认写则先进 confirm_gate,
  确认后由 route_after_confirm/tool_exec 转回 subagent_wait(§6.3)→ interrupt({kind:"subagent_wait"})
  → graph_result:run.status=awaiting_subagent + run.subagent.waiting.v1
子 run 终态 → subagent_wake.notify_parent(父仍 awaiting_subagent 才生效)→ 入队 subagent job
worker 取 job → resume_subagent:子 run 未全终态则收手等下一次;全终态则
  Command(resume={kind:"subagent_wait", children:[...]}) → 回执追加为 user 消息 → 图继续
```

**关键语义**

- **回执用 user 角色消息**:spawn 的 ack 已占用该 `tool_call_id`,再补一条同 id 的 `tool`
  消息会违反 OpenAI 消息协议;回执因此是「[子代理结果回执]」开头的 user 消息,并同时落
  `subagent_notices` 供回放。
- **唤醒幂等**:`notify_parent` 只在父 run 处于 `awaiting_subagent` 时入队;`resume_subagent`
  先把状态改 `running` 并落 `run.subagent.resumed.v1`,后续同 run 的唤醒 `from_job` 直接返回。
- **不得去重**:`enqueue(..., dedupe=False)`——两个子 run 同时终态时,`find_queued` 合并成一条
  会让 worker 在后一个子 run 提交前检查完而吃掉唤醒(父 run 永久挂起)。
- **同批多 spawn**:一批里多个 spawn 一起进 `pending_subagents`,全部子 run 终态才继续
  (没有「任一完成即继续」的策略)。
- **写确认与 spawn 并存**:同回合两者都有时写确认优先,但回执不再丢——`execute` 同时返回
  `pending_writes` 与 `pending_subagents`,确认后 `tool_exec` 收下 spawn 的 ack 再转
  `subagent_wait`(§6.3)。

**残余(如实)**

- 子 run 停在 `awaiting_*`(需人工)不唤醒父 run;唤醒丢失由 §6.2 的等待 sweeper 兜(漏唤醒
  补偿 + 超时级联取消),但**父 run 恢复后不会再等**:超时是终局,父 run 拿 cancelled 回执继续;
- 父 run 在 `running` 中途崩溃由 recover 兜,恢复后可能重复 interrupt 回到 `awaiting_subagent`;
- 子 run 的 `timeout_seconds` 只约束 spawn 内联模式;异步模式的时限由 §6.2 的
  `subagent_wait_timeout_seconds` 在**等待方**计时。

## 6.2 等待超时与漏唤醒补偿(L2 ④,2026-11-17)

`services/subagent_wait_sweeper.py` 每 `subagent_wait_sweep_interval_seconds`(默认 30s)扫一轮
`awaiting_subagent` 的父 run,**只在 `jobs_enabled` 时启动**(没有 job 队列就没人消费唤醒):

1. **漏唤醒补偿**:子 run 已全部终态,但父 run 没有任何 live job(`JobRepository.
has_live_for_run` 为假,即 queued 或租约未过期的 running)→ 重排一条 `subagent` job
   (`dedupe=False`)。典型现场:子 run 终态提交后进程崩,`notify_parent` 的入队随事务一起丢。
2. **等待超时**:仍有非终态子 run,且**最老**子 run 的 `created_at` 距今超过
   `subagent_wait_timeout_seconds`(默认 3600s;`<= 0` 不限时)→ 对每个非终态子 run 调
   `cancel_run(..., WAIT_TIMEOUT_REASON)`,再复核一次 live job,没有则补一条唤醒 job。
   父 run 拿到 `cancelled` 回执后继续(不是永久挂起)。

**为什么超时从等待方发起**:异步模式下子 run 是一条独立 job,没有谁在它身上计时;`cancel_run`
已负责取消其排队 job、落 `run.cancelled.v1`、通知工具侧放弃等待(§9.5),但仍**不打断**已在沙箱
执行的 argv。级联取消是「放弃等待」而不是「杀掉子 run 的进程」。

**幂等**:`resume_subagent` 先把父 run 改 `running` 再续图,重复唤醒直接返回;因此 sweeper 可以
放心重排。取消 N 个子 run 时 `cancel_run` 内部的 `notify_parent` 已各入队一次,末尾的
`has_live_for_run` 复核保证不再多补。

**残余**

- sweeper 与 worker 有竞态窗口:扫到「无 live job」到入队之间,原唤醒 job 可能刚好被写入
  (重复唤醒无害);
- 超时判据用最老子 run 的 `created_at`,不看父子链路是否真在等(父 run 若因别的原因长时间
  停在 `awaiting_subagent`,同样会被判超时);
- `subagent_wait_timeout_seconds <= 0` 时只做漏唤醒补偿,等太久仍只能靠人工取消;
- sweeper 每轮跨账号全表扫 `awaiting_subagent`(与崩溃续跑 sweeper 同口径),量大时需要索引。

## 6.3 写确认与确认闸下的等待回路(2026-11-18)

§6.1 初版留了两个静默降级:同回合「待确认写 + spawn」时 spawn 回执被丢弃;spawn 被账号
审批策略设为 `confirm` 时从 `tool_exec` 执行、根本不进等待节点。两者都不是报错,而是
**异步等待悄悄退化成同步孤儿**——子 run 照跑,父 run 却永远收不到回执。修法是把「回执」与
「确认」解耦成可并存的载荷,并在确认闸两侧都补上回等待节点的边:

1. `execute` 的写确认分支(`graph/nodes/execute.py`)不再只返回 `pending_writes`:同回合
   已识别的 spawn 回执一起写进 `pending_subagents`,所以进确认闸时回执已经落在 state 里。
2. `tool_exec`(`graph/nodes/tool_exec.py`)进入时先继承 `state[PENDING_KEY]`,逐条执行确认后
   的调用,凡输出能解析成 spawn ack(`pending_spawn_ack`)就追加;返回时**显式**给出
   `pending_subagents`(空列表也写),用新值覆盖旧 state,避免上一批回执被重复消费。
3. 路由(`graph/builder.py`)新增 `route_after_tool_exec`:有回执去 `subagent_wait`,否则回
   `execute` 继续下一轮工具调用;`route_after_confirm` 从「批准→tool_exec,否则→verify」
   改为「批准→tool_exec;拒绝但有回执→subagent_wait;否则→verify」——**拒绝写操作不该连带
   丢掉已入队的子代理**,子 run 已经在跑,父 run 应当等它的回执而不是直接进 verify。

于是四条路径都收敛到 `subagent_wait`:纯 spawn(execute 直接路由)、写+spawn(确认后)、
spawn 走 confirm 策略(确认后经 tool_exec)、以及拒绝写但仍有 spawn(confirm 直接路由)。

**语义取舍**

- 回执的收集点是「spawn 工具的输出」而不是「调用意图」:确认被拒时 `tool_exec` 不会执行该
  调用,自然也不产生回执——所以只有**已执行**的 spawn 才会被等待,拒绝后的 spawn 不会留下
  悬空等待。
- `pending_subagents` 只增不减,直到 `subagent_wait` 节点消费并清空;跨两个节点传递依赖
  LangGraph 的 state 合并,所以两边都必须显式返回该键(缺键会让旧值以「未变」语义保留,
  这正是初版丢回执的机制之一)。

**残余**

- 一次确认后仍可能有后续工具轮次继续 spawn(最多 `MAX_TOOL_ROUNDS=12`),这些新回执与旧回执
  一起等;**全部**子 run 终态才继续,没有「先到先续」;
- `subagent_wait` 只有一个中断点,确认与等待发生在同一 turn 的不同 interrupt 上,前端需按
  `kind` 区分 `confirm` 与 `subagent_wait` 两种待办(当前 `awaiting_confirmation` /
  `awaiting_subagent` 已是两个状态,故不冲突)。

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
- **run 级**两个新事件:`run.subagent.waiting.v1`(payload `children`:`[{child_run_id,title}]`,
  父 run 进入 `awaiting_subagent` 时落)与 `run.subagent.resumed.v1`(payload `children`:`[child_run_id]`,
  全部子 run 终态、父 run 重新 `running` 时落);两者 `record_outbox=False`,与过程项同为本地投影;
- `event-taxonomy.md` 同步:§4 增 `subagent` 行,§7 增发射点。

## 8. API

| 端点                              | 返回                          | 说明                                                           |
| --------------------------------- | ----------------------------- | -------------------------------------------------------------- |
| `GET /api/v1/runs/{id}/subagents` | `list[SubagentRead]`          | 子代理列表(按创建时间);先 `require_run`(跨账号 404);空数组合法 |
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

- **继承**:account、`workspace_id`、`mcp_server_ids`、账号级审批策略、LLM 路由;
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
- `tests/test_subagents_async.py`(L2):ack 识别契约、等待节点 interrupt 载荷与回执形态、
  队列化(spawn 只入子 run 的 plan job)、唤醒条件(父 `awaiting_subagent` 才入队)、
  子 run 未全终态时不推进、全终态时 resume 载荷顺序与 item 终态补写;
- `tests/test_subagent_wait_sweeper.py`(L2 ④):漏唤醒补偿(子全终态 + 无 live job → 重排
  `subagent` job)、已有 live job 不重复入队、未超时不打扰在飞子 run、超时级联取消子 run 且父 run
  拿到唤醒、父 run 不在等待态与没有子 run 时跳过;
- `tests/test_subagent_wait_routing.py`(§6.3):`execute` 同回合 danger+spawn 时两种载荷并存、
  `tool_exec` 从确认后的 spawn 输出收 ack 并与 state 里旧回执合并、`route_after_tool_exec` 与
  `route_after_confirm` 三种状态的路由、两条 e2e(写+spawn 同回合确认后落 `awaiting_subagent`、
  spawn 走 confirm 策略确认后才落 `awaiting_subagent` 且确认前 `/subagents` 为空);
- `tests/snapshots/openapi.json` 重生成(新端点 + RunRead 字段);
- 前端 `run-item-timeline.test.tsx`(subagent 卡片)、`run-detail.test.tsx`
  (子代理面板)、`runs-api.test.ts`(subagents/usage);
- 门禁:`npx nx lint/format agent-orchestrator`、`npx nx test agent-orchestrator`、
  `npx nx typecheck/lint/test @beauty/web`、`scripts/check-docs.sh`、
  `scripts/check-workspace.sh`。

## 14. 后续

- 子代理可选更便宜模型(per-run 选择已随 P1-G 落地;子代理当前沿用父 run 模型);
- 多子代理并发编排的调度策略(当前只做「全终态才继续」);
- 命名子代理注册表(可复用的 agent 模板与权限包);
- AG-UI `Subagents` 事件族映射与跨端渲染。
