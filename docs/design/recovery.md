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

`POST /v1/runs/{id}/rewind`，载荷 `{checkpoint_id, reason?}`：

1. `TRANSITIONS["rewind"] = {completed, failed, cancelled, blocked}`（运行中/待审批的 run 必须先 `cancel`，避免与在飞的图调用并发写状态）。
2. 取消该 run 未领取的 job；`status=running`，清 `status_reason`；**attempts 不变**（线程不变，才能从该检查点续跑）。
3. 记 `run.rewind.v1`：`{checkpoint_id, from_status, reason}`。
4. `jobs_enabled` → 入队 kind `rewind`（`KINDS` 加一项），payload `{checkpoint_id}`；否则同步执行后 `_apply_graph_result`。
5. worker 执行：`graph.ainvoke(None, config=_config(run) + {"configurable": {"checkpoint_id": ...}})`，失败走 `_fail_run(stage="rewind")`。
6. 归属校验：checkpoint 必须属于当前线程 `run.id#attempts`，否则 409 `run.checkpoint_not_found`。

**语义已验证**（LangGraph 本地 spike）：同一 `thread_id` 上带 `checkpoint_id` 调 `ainvoke(None, config)` 只重跑该检查点之后的节点，不从头重来。

### 5.2 fork（从任意检查点派生新 run）

`POST /v1/runs/{id}/fork`，载荷 `{checkpoint_id?, title?}` → `201 RunRead`：

- 新 run：`parent_run_id=id`、`parent_kind="fork"`（`models/runs.py` 加常量）、`status=pending`，继承 `goal/acceptance_criteria/skill 快照/mcp_server_ids/model/conversation_id/workspace_id` 与 `plan_markdown`（与 retry 同构），`priority/timeout_seconds` 沿用父 run。
- 不复制事件、交付物、用量（新 run 新时间线，与 retry 一致）；`run.created.v1` 载荷加 `{forked_from, checkpoint_id?}`。
- 给了 `checkpoint_id` 时做状态种入：`aget_state(父 config + checkpoint_id)` 取 `values`，`aupdate_state(子 config, values, as_node=<前置节点>)`。前置节点 = 目标状态的历史上一条的 `next[0]`（`next` 不唯一时**降级为输入继承**并记 warning）。**已验证**：`as_node` 决定从哪个节点的后继继续，`as_node="a"` 时只跑 `b→c`。
- 覆盖 `title` 可选（默认 `"{父标题}（分叉）"`）；源 run 状态不限（只读父状态，不改源 run）。

### 5.3 状态机与事件汇总

新增动作：`timeout`（非终态 → failed，sweeper 专用）、`rewind`（`completed|failed|cancelled|blocked` → running）；`fork` 不改源 run，不查 `TRANSITIONS`。
新增事件类型：`run.timeout.v1`、`run.rewind.v1`；`run.created.v1` 载荷扩展 `forked_from`。
新增 job kind：`rewind`。

## 6. 测试与验收

- C1：sweeper 单元测试（生效值/基准兜底/条件更新落空跳过/取消队列 job/事件与指标）；`is_superseded` 用例（超时后图返回 completed 被丢弃）；priority 排序用例（同队列高优先级先领）；迁移 upgrade/downgrade/`alembic check` clean。
- C2：检查点列表（有/无历史、limit 边界、账号隔离 404、runnable 标记）；不影响既有 1049+ 用例。
- C3：rewind 后只跑后续节点（注入可观测 stub）、状态机非法迁移 409、线程归属校验；fork 继承字段、事件载荷、状态种入（`as_node` 路径）与降级路径。
- 前端：C1 建 run 表单 + 详情超时/优先级；C2/C3 检查点页签与两个动作按钮；`/runs` 列表超时徽标。

## 7. 非目标与残余

- 不做"在飞图调用的强制中断"（LangGraph 节点是纯函数，强制取消会破坏状态一致性；协作式收尾已足够）。
- 不做"跨 attempts 的检查点回退"（checkpoint 只属于某个线程；需要跨线程搬运时用 fork）。
- 检查点列表不分页游标（`limit` + `before` 后续再加），不回传完整图状态（避免大 payload 与内部字段泄漏）。
- 超时扫描多副本下依赖条件更新去重，允许极端竞态下一轮重复记 warning（不会重复记 `run.timeout.v1`，因为更新未命中即跳过）。
