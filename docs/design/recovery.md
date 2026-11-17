# 恢复语义（P1-C）设计

> 状态：**契约已全部落地（2026-11-10）**。范围：run 墙钟超时、优先级、检查点列表、回退（rewind）与分叉（fork）。
> 分三阶段实施：**C1 超时 + 优先级** → **C2 检查点列表** → **C3 rewind/fork**。每阶段独立可交付、可回滚。
>
> 落地位置：迁移 `e3c9a1f7b5d2`（runs.timeout_seconds/priority/started_at + jobs.priority + 索引）、
> 超时 sweeper `services/run_timeout_sweeper.py`、检查点/回退/分叉 `services/run_checkpoints.py` 与
> `services/run_recovery.py`、端点 `GET /api/v1/runs/{id}/checkpoints`、`POST …/rewind`、`POST …/fork`、
> job kind `rewind`、事件 `run.timeout.v1`/`run.rewind.v1`。残余见 §10。

## 1. 现状与约束

- **checkpointer 已就绪**：Postgres 用 `AsyncPostgresSaver`（`core/container.py:231-244`，`setup()` 幂等建表），SQLite/dev 回退 `InMemorySaver`（`graph/builder.py:88`）。
- **图线程标识** `f"{run.id}#{run.attempts}"`（`services/run_execution_approvals.py:165` `_config`）：同一 run 每次 blocked→resume 周期是一个新线程；retry 是另起 run（新 id），所以线程互不重叠。
- **取消是协作式的**（`services/run_cancel.py`）：状态先落 `cancelled`，执行边界回来时由 `_apply_graph_result` 的 `is_cancelled`（`run_execution_approvals.py:214`）丢弃迟到结果，不强行中断图调用。
- **retry 不在原地重放**（`run_execution.py:128+`）：`failed` 是终态，重试派生新 run 并继承输入与已批准计划；旧 run 的历史原样保留。P1-C 的 rewind 是**同 run 原地回退**，与该惯例不冲突（rewind 的检查点由用户显式指定，不是"在失败点盲目重放"）。
- **缺口**：`runs` 无 `timeout_seconds`/`priority`/`started_at` 列；`jobs.claim_next` 只按 `run_at, created_at` 排序（`repositories/job.py:24`）；无检查点/回退/分叉端点。
- **两套 thread_id 不要混**：过程项投影用 `items.resolve_thread_id`（会话 id 或沿父 run 上溯到根），与图线程 `run.id#attempts` 是不同概念；检查点 API 只认后者。

## 2. 数据模型

一次迁移（`down_revision` = 当前 head，P1-B 落地后确认）新增四列 + 一个索引，全部可空或带 `server_default`，旧行安全：

| 表   | 列                | 类型                         | 说明                                                   |
| ---- | ----------------- | ---------------------------- | ------------------------------------------------------ |
| runs | `timeout_seconds` | Integer NULL                 | 本 run 墙钟上限（秒）；`NULL` = 用平台默认，`0` = 不限 |
| runs | `priority`        | Integer NOT NULL default 0   | `-100..100`，越大越先执行；快照到 job 列               |
| runs | `started_at`      | DateTime(timezone) NULL      | 首次离开 `pending` 的时刻（超时基准，见 §3.2）         |
| jobs | `priority`        | Integer NOT NULL default 0   | 入队时从 `run.priority` 复制；独立成列才能被索引排序   |
| jobs | 索引              | `(status, priority, run_at)` | 领取按 `priority DESC, run_at, created_at` 排序        |

`RunRead` 增 `timeout_seconds`/`priority`/`started_at`；`RunCreate` 增 `timeout_seconds`（`ge=0`，上限 604800）与 `priority`（`ge=-100, le=100`）。老客户端不传走默认，不破坏契约。

## 3. C1：墙钟超时 + 优先级

### 3.1 配置（`core/config.py`）

```text
run_timeout_seconds=0                    # 平台默认墙钟；0=不限，run 级列可覆盖
run_timeout_sweep_interval_seconds=30.0  # 扫描间隔
```

默认关闭（`0`）是刻意的：超时会杀掉正在干活的长任务，必须由部署方或用户显式开启。

### 3.2 生效超时与基准时间

- 生效值 = `run.timeout_seconds` 非空 ? 它 : `settings.run_timeout_seconds`；`0` 表示不限。
- 基准 = `coalesce(run.started_at, run.created_at)`：`started_at` 在首次进入 `planning`/`running` 时落库（两条入口：`plan_run` 与 `approve_run` 的 `RUNNING` 转换处；只写一次，存在则不覆盖），扫描时用 `created_at` 兜底，覆盖"排队中一直没人审批"的情形。
- 语义：超时 → **`failed`**（不是 `cancelled`、不是 `blocked`）。理由：failed 可 `retry`（派生新 run 继承上下文），且明确表达"系统因限时终止"，不冒充用户取消。

### 3.3 超时收尾（新后台任务 `services/run_timeout_sweeper.py`）

扫描条件（索引走 `runs.status`）：

```text
status IN (pending, planning, running, awaiting_approval, awaiting_confirmation, awaiting_input, blocked)
AND 生效超时 > 0
AND coalesce(started_at, created_at) + 生效超时 <= now
```

单条收尾必须在同事务内**先原子改状态再写事件**，防止多副本重复收场：

1. `UPDATE runs SET status='failed', status_reason=..., updated_at=now WHERE id=:id AND status=:observed_status`（条件更新，命中 0 行说明有人先处理了 → 跳过）；
2. 记 `run.timeout.v1`，载荷 `{timeout_seconds, started_at, elapsed_seconds, from_status}`；
3. `JobRepository.cancel_queued_for_run` 取消未领取的 job；
4. `metrics.observe_run_outcome(status="failed")`；日志带 `run_id/account_id/elapsed_seconds`。
5. `status_reason` 固定文案：`"执行超时（{n} 秒），可重试"`（截 500）。

启动条件与 interrupt sweeper 一致（`jobs_enabled` 且任一开关 > 0 时才起任务），互不影响；扫描是幂等的，单条失败只记日志不中断整轮。

### 3.4 协作式收尾（关键正确性）

超时只改库，不打断已发出的图调用；调用返回时若目标 run 的状态已是 `cancelled` **或 `failed`**，必须整体丢弃结果（不写 `completed`、不落交付物）。实现：把 `_apply_graph_result` 的 `is_cancelled` 判定改为通用的 `is_superseded(session, run)`（重新读库；`cancelled`/`failed` 视为已被别的路径收场），原有 cancel 路径语义不变。同样要覆盖 confirm/resume/answer 等执行边界的迟到结果。

### 3.5 优先级

- `enqueue` 写 `jobs.priority = run.priority`（在 `services/jobs.py` 的入队处取，允许调用方覆盖：spawn 子代理时可用 `PARENT` 的优先级）。
- `claim_next` 排序改 `priority DESC, run_at, created_at`，索引见 §2；PG 的 `FOR UPDATE SKIP LOCKED` 与租约逻辑不变。
- 前端：建 run 时可填超时（分钟）与优先级（低/普通/高，映射 -10/0/10）；run 详情显示超时倒计时（`started_at + 生效超时`）与优先级标签；超时的 run 显示 `status_reason` 与「重试」入口。

## 4. C2：检查点列表（只读）

`GET /v1/runs/{id}/checkpoints?limit=50`（`1..200`，默认 50）：

- 账号隔离走 `require_run`；线程 = `_config(run)` 的 `thread_id`。
- 数据源：`graph.aget_state_history(config, limit=limit)`（LangGraph 公开 API；PG/dev 两个 saver 都支持）。
- 响应 `CheckpointRead`：`checkpoint_id`、`step`、`source`、`next: list[str]`、`created_at`（取状态里 `ts`，缺失回退 `run.updated_at`）、`parent_checkpoint_id`、`runnable: bool`（`next` 非空）、`summary`：`{rounds, plan_chars, tool_calls, todos_count, artifact_ready}`（只投影标量，不回传整份图状态）。
- 读端点不写 `audit_logs`（与 admin-plane 口径一致）；404 沿用 `run.not_found`。
- 前端：run 详情抽屉新增「检查点」页签，列表按 `step` 倒序，展示下一步/时间/摘要，行内「回退到此」「从此分叉」按钮（C3 落地后启用）。

## 5. C3：回退与分叉

### 5.1 rewind（同 run 原地回退）

`POST /v1/runs/{id}/rewind`，载荷 `{checkpoint_id, reason?, acknowledge_side_effects?}`：

1. `TRANSITIONS["rewind"] = {completed, failed, cancelled, blocked}`（运行中/待审批的 run 必须先 `cancel`，避免与在飞的图调用并发写状态）。
2. 取消该 run 未领取的 job；`status=running`，清 `status_reason`；**attempts 不变**（线程不变，才能从该检查点续跑）。
3. 已发生写工具调用且未传 `acknowledge_side_effects=true` → 409 `run.side_effects_present`（台账见 §10）。
4. 记 `run.rewind.v1`：`{checkpoint_id, from_status, reason, side_effects}`。
5. `jobs_enabled` → 入队 kind `rewind`（`KINDS` 加一项），payload `{checkpoint_id}`；否则同步执行后 `_apply_graph_result`。
6. worker 执行：`graph.ainvoke(None, config=_config(run) + {"configurable": {"checkpoint_id": ...}})`，失败走 `_fail_run(stage="rewind")`。
7. 归属校验：checkpoint 必须属于当前线程 `run.id#attempts`，否则 409 `run.checkpoint_not_found`。

**语义已验证**（LangGraph 本地 spike）：同一 `thread_id` 上带 `checkpoint_id` 调 `ainvoke(None, config)` 只重跑该检查点之后的节点，不从头重来。

### 5.2 fork（从任意检查点派生新 run）

`POST /v1/runs/{id}/fork`，载荷 `{checkpoint_id?, title?, acknowledge_side_effects?}` → `201 RunRead`（给了 `checkpoint_id` 且父 run 有写工具调用时同样受 §10 的 409 闸约束）：

- 新 run：`parent_run_id=id`、`parent_kind="fork"`（`models/runs.py` 加常量）、`status=pending`，继承 `goal/acceptance_criteria/skill 快照/mcp_server_ids/model/conversation_id/workspace_id` 与 `plan_markdown`（与 retry 同构），`priority/timeout_seconds` 沿用父 run。
- 不复制事件、交付物、用量（新 run 新时间线，与 retry 一致）；`run.created.v1` 载荷加 `{forked_from, checkpoint_id?, side_effects?}`。
- 给了 `checkpoint_id` 时做状态种入：`aget_state(父 config + checkpoint_id)` 取 `values`，`aupdate_state(子 config, values, as_node=<前置节点>)`。前置节点 = 目标状态的历史上一条的 `next[0]`（`next` 不唯一时**降级为输入继承**并记 warning）。**已验证**：`as_node` 决定从哪个节点的后继继续，`as_node="a"` 时只跑 `b→c`。
- 覆盖 `title` 可选（默认 `"{父标题}（分叉）"`）；源 run 状态不限（只读父状态，不改源 run）。

### 5.3 状态机与事件汇总

新增动作：`timeout`（非终态 → failed，sweeper 专用）、`rewind`（`completed|failed|cancelled|blocked` → running）、`recover`（`planning|running` → 续跑收尾，内部动作，见 §8）；`fork` 不改源 run，不查 `TRANSITIONS`。
新增事件类型：`run.timeout.v1`、`run.rewind.v1`、`run.recovered.v1`；`run.created.v1` 载荷扩展 `forked_from`。
新增 job kind：`rewind`、`recover`。

## 6. 测试与验收

- C1：sweeper 单元测试（生效值/基准兜底/条件更新落空跳过/取消队列 job/事件与指标）；`is_superseded` 用例（超时后图返回 completed 被丢弃）；priority 排序用例（同队列高优先级先领）；迁移 upgrade/downgrade/`alembic check` clean。
- C2：检查点列表（有/无历史、limit 边界、账号隔离 404、runnable 标记）；不影响既有 1049+ 用例。
- C3：rewind 后只跑后续节点（注入可观测 stub）、状态机非法迁移 409、线程归属校验；fork 继承字段、事件载荷、状态种入（`as_node` 路径）与降级路径。
- 前端：C1 建 run 表单 + 详情超时/优先级；C2/C3 检查点页签与两个动作按钮；`/runs` 列表超时徽标。
- S1（11-17）：两次 attempt 同工具同参只执行一次（`tests/test_tool_idempotency.py`）。
- S3/S4（11-17）：取消在节点边界抛 `RunCancelled`、步骤在 `ainvoke` 边界前即可见（`tests/test_cancel_interrupt.py`、`tests/test_live_steps.py`）。
- S5（11-17）：台账只列写工具（失败写也计）、未确认 rewind/fork 409 且不重跑、确认后事件载荷带 `side_effects` 汇总（`tests/test_rewind_side_effects.py`）。

## 7. 非目标与残余

- 不做"在飞图调用的强制中断"：取消在**安全边界**生效（节点入口、模型调用前、工具调用前，见 §9），不劈半一次工具调用；已发生的写由稳定幂等键兜底（[tool-service.md](tool-service.md) §5.3）。
- 不做回退的**副作用补偿**：只做台账 + 显式确认（§10），真正的撤销执行器后续再说。
- 不做"跨 attempts 的检查点回退"（checkpoint 只属于某个线程；需要跨线程搬运时用 fork）。
- 检查点列表不分页游标（`limit` + `before` 后续再加），不回传完整图状态（避免大 payload 与内部字段泄漏）。
- 超时扫描多副本下依赖条件更新去重，允许极端竞态下一轮重复记 warning（不会重复记 `run.timeout.v1`，因为更新未命中即跳过）。

## 8. 崩溃续跑（recover，2026-11-17）

### 8.1 断点在哪

图执行过程中 run 状态会随增量投影提前提交：`services/run_events.py` 的 `_delta_stream` 进入与每批 flush、`_item_stream` 进入都 `commit`。所以进程崩溃时，run 可能已落库为 `planning/running`，而正在执行的 job 所在事务回滚——队列里那条 job 记录也随之消失。旧 job 若被租约超时后重领，按原动作（plan/approve/confirm/resume）重放必然被 `require_transition` 拒成 409，重试耗尽后 job 落 failed，run 永久挂在执行中（超时 sweeper 默认关闭，兜不住）。

### 8.2 `recover_run`

`services/run_recovery.recover_run(session, graph, account_id, run_id, *, from_job=True, jobs_enabled=False, ...)`：

1. 状态必须是 `planning/running`（`RECOVERABLE_STATUSES`）。`from_job` 时若已不是这两个状态就直接返回（期间被取消/超时/另一条恢复收尾）；API 路径则 409 `run.invalid_state`。
2. `graph.aget_state(thread_config(run))` 取最近 checkpoint；线程仍是 `run.id#attempts`，**attempts 不变**。
3. 按现场分三路：
   - **tasks 带 interrupt**：图停在人工闸口但状态没落库。不重跑已中断的节点，把 interrupt 塞回 values 交给 `_settle_recovered_run`（计划闸 → `awaiting_approval` + `run.planned.v1 {recovered: true}`；提问闸 → `awaiting_input`）。
   - **`next` 为空**：图已跑完、只是结果没落库。直接用快照 values 投影终态，不再调模型。
   - **`next` 非空**：`ainvoke(None, config)` 从 checkpoint 继续；投影上下文与 rewind 相同（delta/step/item/usage），异常走 `_fail_run(stage=recover)`。
4. **没有 checkpoint**（崩在第一次落 checkpoint 之前）：退化为新 attempt 重新规划（`attempts += 1`、`pending`、记 `fallback: plan`），与旧 `resume` 语义一致。
5. 每次都落 `run.recovered.v1`：`{attempt, from_status, checkpoint_id, next, resumed, fallback}`。`resumed=false` 表示没执行图（闸口补落 / 快照收尾 / 无 checkpoint 退化）。

### 8.3 两条触发

- **worker 改道**：`jobs_worker._dispatch` 对 `plan/approve/confirm/resume` 且 `job.attempts > 1`（已被领取过一次、说明执行中崩过）的 job，先看 run 状态；若已停在 `planning/running`，该动作的前置状态确实被崩溃写坏，重放只会在 409，于是改调 `recover_run(from_job=True)`。**首次领取不能改道**：API 侧 approve/confirm 也是先把状态推到 `running` 再入队，那是正常的待执行现场。若还有别的 live job（queued，或 running 且租约未过期）持有该 run，则本 job 直接返回（日志 `job superseded by live job`），避免两条 job 抢同一条图线程。
  - `answer`/`rewind` 不拦截：API 侧合法地把状态先推到 running 再入队，拦截会丢掉 `Command(resume)` 语义。
- **sweeper**：`services/run_recovery_sweeper.py` 每 `run_recovery_sweep_interval_seconds`（默认 30s，<=0 关闭）扫「`planning/running` 且 `updated_at` 早于 `run_recovery_grace_seconds`（默认 60s）且 `has_live_for_run` 为假」的 run，入队 kind `recover`。它覆盖「job 丢了没人重领」「job 重试耗尽已 failed」的情形。与超时 sweeper 的分工：这里只把执行接回正轨，不判失败。

### 8.4 配置与状态机

- 新增配置（`core/config.py`，`AGENT_ORCHESTRATOR__` 前缀）：`run_recovery_grace_seconds=60`、`run_recovery_sweep_interval_seconds=30.0`（interval<=0 关闭 sweeper，默认随 `jobs_enabled` 启动）。
- `TRANSITIONS` 新增内部动作 `recover`（`planning|running` 起步，不暴露 API）；`KINDS` 新增 `recover`；新增事件 `run.recovered.v1`。
- 复用既有租约语义：`JobRepository.has_live_for_run(account_id, run_id, now, exclude_job_id=...)` 把「queued」与「running 且 `lease_until >= now`」视为 live；租约过期的 running 不算 live（与 `claim_next` 的回收口径一致）。

### 8.5 残余

- **没有 fencing**：恢复的进程若与其实还活着的原进程并发，会在同一线程上各跑一遍（grace + live job 检查只把窗口压小，不是互斥）。多副本下「查 live job」与租约过期之间存在竞态。
- `awaiting_*` 不恢复：那是合法休眠，不是崩溃现场，由 interrupt sweeper 按超时处理。
- retry 派生新 run 不受保护：崩溃发生在 retry 建 run 与新 job 之间时，子 run 停在 pending（无 checkpoint），sweeper 只扫 planning/running，这类需人工重跑。
- 对话直答/确认端点等非图路径不涉及续跑。

## 9. 取消在节点边界生效（S3，2026-11-17）

### 9.1 此前为什么不打断

旧实现只把 `runs.status` 写成 `cancelled` 并清掉排队 job（`services/run_cancel.py`）；正在执行的图完全不知道，会跑完当前节点乃至整条链路，直到下一次状态迁移撞上 `require_transition` 才暴露。用户在界面上点了取消，模型和工具还在烧 token。

### 9.2 实现：库里状态是唯一信号

- **不引入进程内事件**：取消可能来自另一个进程/副本（API 与 worker 分进程部署），所以探针直接查库。
- graph 侧新端口 `CancellationPort`（`graph/ports.py`，默认 `_NullCancellation` 恒假）+ `graph/cancellation.py` 的 `raise_if_cancelled(runtime)` → 抛 `RunCancelled`。问的时机只在安全边界：节点入口（`graph/steps.py` 的 `instrument_node`）、每轮模型调用前与每次写工具执行前（`graph/nodes/execute.py`）、每次工具调用前（`graph/nodes/tool_exec.py`）。**不在一次工具调用中途打断**：写要么已发生要么没发生，已发生的由 S1 稳定幂等键保证重放不重跑。
- services 侧 `RunCancellationProbe(session, run_id)`（`services/run_cancellation.py`）每次直接 `select(Run.status)`，命中 `cancelled` 后记忆；只读一次/边界。`_config(..., session=...)` 统一装配探针与边界提交（见 §9.3），各执行入口（plan/answer/approve/confirm/recover/rewind）都传执行会话。
- 终止处理：落点各自 `except RunCancelled` → `settle_cancelled(session, run, stage=...)`，只 refresh 并返回，**不写 failed、不覆盖取消方写好的 `status_reason`**；普通异常路径的 `_fail_run` 也被 `is_superseded` 挡住。worker 的 `process_one` 另有兜底分支（`job stopped by cancellation` + `mark_succeeded`），避免 job 被当成失败反复重试。

### 9.3 边界提交（关键约束）

取消方要能写进 `runs.status`，执行方就不能整段图持着未提交事务：SQLite 是库级写锁，Postgres 下执行方若改过同一行也会阻塞。实测这一条就是 S3 首版的拦路虎——取消请求在 `UPDATE runs` 上等锁 500（`database is locked`）。

因此新增 `BoundaryPort`（`graph/ports.py`，默认空实现）+ `services/run_boundary.py` 的 `SessionBoundary`：`instrument_node` 在**每个节点开工前** `commit`，顺序是「边界提交 → 取消探针 → 节点体」。

- 与 checkpoint 的分工：checkpoint 记「图走到哪」（可续跑）；边界提交让事件/过程项/状态对别的事务可见（取消才看得见、审计才读得到）。
- `database/session.py` 的 `async_sessionmaker(engine, expire_on_commit=False)` 保证提交不会让 `run` 等 ORM 属性过期；`run_events.py` 的 `_delta_stream`/`_item_stream` 早已中途 commit，这里只是把同一做法推广到节点边界。
- 代价：节点之间的部分进展先落库，节点失败时这些行不回滚（与既有增量投影一致，不是新引入的语义）。

### 9.4 测试与残余

`tests/test_cancel_interrupt.py`：3 个节点级用例（恒真/恒假探针 + `instrument_node` 包装，断言 `RunCancelled` 抛在节点体之前）+ 1 个集成用例（blocking FakeLLM，plan→approve→图在模型调用中挂住→另一会话取消→放行后断言 run 仍 `cancelled`、没有再落 `run.failed.v1`/`run.completed.v1`、模型调用次数停在 2）。

- **粒度是边界**：S3 时一次很长的模型/工具调用不会被劈开，取消在下一个检查点才生效（对用户是秒级到一次调用时长）；S6 起执行期外呼也可以放弃等待（见 §9.5），但仍不会打断沙箱里已经跑起来的命令。
- 每个边界一次 `SELECT`；要压成本只能加短 TTL 缓存，代价是取消延迟。
- `graph.aupdate_state` 的配置不带会话（不跑节点），将来若在 aupdate_state 里跑节点需要补。
- 取消与 recover 并发：`recover_run` 只接 `planning/running`，已取消的 run 不会被接回；但两者与 rewind 的交互仍靠状态机而非互斥。

### 9.5 执行期也能放弃等待（S6，2026-11-17）

S3 只在节点边界问状态，所以「一次很长的模型调用/工具调用」期间取消要等它自己结束；如果工具真的卡住（模型网关不返回、沙箱命令长时间跑），取消就没意义了。

- **`graph/cancellation.py` 的 `guarded(runtime, start, *, poll_interval=GUARD_POLL_SECONDS)`**：把外呼包成 `asyncio.ensure_future(start())`（`start` 是零参可调用，避免未 await 协程告警），用 `asyncio.wait({task}, timeout=poll_interval)` 分片等待；每片之间问一次探针（默认 0.25s），已取消就 `task.cancel()` 并抛 `RunCancelled`，正常完成则返回 `task.result()`，其他异常原样抛出（**不吞**）。
- **覆盖点**（都是"可能长时间不返回的外呼"）：`graph/nodes/execute.py` 的 `llm.complete(...)` 与 `toolkit.execute(...)`、`graph/nodes/tool_exec.py` 的 `toolkit.execute(...)`、`graph/plan_nodes.py` 的 `_text_reply`（非流式 `complete` 与流式每次 `anext`）。
- **两级协作**：orchestrator 放弃等待之后，tool-service 那边还在跑。所以 `cancel_run` 会顺带通知 tool-service（`ToolClient.cancel_tools` → `POST /v1/tool-calls:cancel`），把该 run 下 `status=running` 的审计行标 `cancel_requested`；tool-service 执行期间按 `cancel_poll_seconds` 轮询这个标记，看到就放弃等待、把行落 `status=cancelled` 并回 `tool.cancelled` 信封。通知是**旁路**：`configure_tool_cancel` 注入，`NOTIFY_TIMEOUT_SECONDS=2.0`，超时或异常只 `logger.warning`，绝不让取消失败。
- **探针用独立短连接读**（`services/run_cancellation.py` 的 `RunCancellationProbe`）：执行期的轮询与执行本身并行，若复用执行会话，每次 `SELECT` 都会在执行会话上留下一个未结束的读事务——SQLite 下读事务持 SHARED、取消方持 RESERVED，两边 commit 互等，直接撞成 `database is locked`，随后同一会话的后续语句连环 `PendingRollbackError`，一个观测面失败会把整个 job 打死。探针因此按 `session.bind` 另开 `AsyncSession`、读完即关；读失败（锁冲突/抖动）只记 warning 并按"尚未取消"处理，下一次轮询或下一个节点边界会再看。
- **失败不扩散**：`_RunStepSink.flush` 的兜底 except 在连接级错误（`PendingRollbackError` / `OperationalError` / `InterfaceError` / `DisconnectionError`）上回滚会话（`_reset_if_invalid`），否则"步骤落库失败"会顺带毒化整个 job 的会话；业务错误不回滚，仍由调用方处理。工具调用期间的取消不算工具失败，`execute`/`tool_exec` 节点对 `RunCancelled` 显式放行，过程项不会被打上 failed。
- **残余**：① 沙箱里已经起的命令不会被打断（`argv` 层面无信号通道），只能等它自己结束或超时；② 同步阻塞在事件循环线程里的调用（例如某些同步 HTTP 库）`task.cancel()` 打不断，只能等它返回；③ 没有 fencing——原执行者若只是"慢"而非"死"，它与接管者可能各写一次（租约机制只缩小窗口，不消除）。

## 10. 回退前的外部副作用台账（S5，2026-11-17）

### 10.1 为什么回退不是无损的

rewind/fork 回退的是**图状态**，不是世界状态：检查点之后已经调用过的写工具（写文件、改 Notion 页面、发消息）不会再被撤销。重跑时：

- 图执行路径的重复写由稳定幂等键挡住——键是 `sha256(run_id|node|tool_name|规范化参数)`，同参重放返回原结果；
- 但**参数变了就是新的外部写**（用户回退往往就是为了改方案，参数必然会变）；
- fork 换 `run_id`，幂等键整体换新，子 run 的写一定重新执行。

所以回退前必须让调用方知道「这个 run 已经写过东西」，并且要求它显式确认。

### 10.2 台账：只读投影，不新增表

`services/run_side_effects.py`：

- `list_side_effects(session, account_id, run_id)` 读 `run.tool.called.v1` 事件，保留 `risk in {write, danger}` 的调用（`WRITE_RISKS`）。**失败/超时/interrupt 的写也算**：工具可能在返回前就已经改了外部状态，没拿到 ok 不等于没发生。
- 每条投影成 `SideEffect(seq, tool_call_id, name, risk, decision, ok, arguments)`，`arguments` 截到 200 字符（`MAX_ARGUMENTS_CHARS`），对外明细最多 20 条（`MAX_DETAILS`）。
- 只读：事件表是唯一事实源，不引额外写路径，也不需要迁移。

### 10.3 闸：409 与显式确认

`require_acknowledged(session, account_id, run_id, *, acknowledge, action)`：有写且 `acknowledge=False` → `ConflictError("run.side_effects_present", details={run_id, action, count, tools, side_effects})`。

- 挂点：`rewind_run`（checkpoint 校验通过后、状态迁移前，只挡 API 路径 `from_job=False`；worker 重入时已确认过）与 `fork_run`（仅当给了 `checkpoint_id`——不带检查点的 fork 不重跑父 run 节点，是无损的）。
- 请求体加 `acknowledge_side_effects: bool = False`（`RewindRequest`/`ForkRequest`）。
- 确认后把汇总写进事件载荷供审计：`run.rewind.v1` payload 增加 `side_effects: {count, tools}`；带检查点的 `run.created.v1` 同样。
- 新读端点 `GET /v1/runs/{id}/side-effects` → `list[SideEffectRead]`，前端在回退/分叉弹窗里先列出来。
- 前端（11-17 已接，`apps/web`）：弹窗打开时取台账（`queryKeys.runSideEffects`，只在弹窗打开时 `enabled`），有写就列出「工具 + 已执行/未成功 + 参数摘要」并要求勾选「我已知悉,仍要继续」，未勾选时确认按钮 `disabled`；提交时只在勾选后带 `acknowledge_side_effects: true`。台账没取到、或提交期又冒出新写，则用 409 的 `details.side_effects` 回填明细（`sideEffectsFromError`）并翻成中文提示。

### 10.4 测试与残余

`tests/test_rewind_side_effects.py`：台账只列写（读不计、失败写也计）；未确认 rewind → 409 且**状态不变、图没重跑**（`len(fake.calls)` 不涨）；确认后 200 且事件载荷带汇总；无写时不用确认；带检查点的 fork 同样要确认、不带检查点的 fork 直接 201。

- 残余：确认是**知会**不是**补偿**，真正的撤销/补偿执行器（记前镜像、跑回滚动作）不在本期。
- 台账只覆盖图执行路径；对话直答、确认端点、MCP 直调三条非图路径的写不进台账（它们本来也没传稳定幂等键）。
- rewind 之后用户改计划导致的新写无法预防——闸只能保证「用户知道自己在重放写过东西的 run」。
- 前端只做「知会 + 拦截」：勾选不落库（审计以事件载荷里的 `side_effects` 为准），也没有补偿入口。
