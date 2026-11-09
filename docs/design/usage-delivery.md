# 用量可靠投递（P0-3）

> 状态：已落地（2026-11-09）。范围：agent-orchestrator 用量事件 → outbox → account-service `usage_events`。
> 相关设计：计价与金额口径见 [cost-attribution.md](./cost-attribution.md)，outbox 机制见 [architecture.md](../architecture.md) 与 `packages/events/events/outbox.py`。

## 1. 问题与目标

旧链路里 `UsageReporter.report()` 在 run 收尾时逐条 POST `/v1/usage/events`，任一条失败就 `return`：

- 同步收尾路径（`run_quota._usage_boundary` 的 finally）已经算完钱，却可能什么都没记下来；
- 失败只留一条 `observe_job_failure(kind="usage_report")` 与 warning，没有重试；
- 接收端没有幂等键，重试会把同一笔用量记两遍，所以也不能简单加重试。

目标：**用量记账至少一次、重复投递不重复计费**，并且入队与业务事务解耦——run 失败/回滚不代表已经消耗的 token 可以不记。

## 2. 不变量

- **至少一次**：先落 outbox（独立短事务提交），再由 dispatcher 重试投递；投递成功才 `mark_dispatched`。
- **幂等键显式**：每个样本带 `event_id = "<outbox 事件 id>:<样本序号>"`；account 按 `(account_id, event_id)` 唯一约束去重，重投命中已有行直接返回。
- **入队与业务事务解耦**：`session_scope(factory)` 单独提交，不参与 run 的事件 append 事务；业务回滚不影响已发生用量的记账。
- **入队失败不阻塞 run**：outbox 写失败时退回旧的逐条直连 POST（best effort），并记 `usage_report` 失败指标；不能因为记账设施故障把 run 判失败。
- **未装配即旧行为**：测试/无 outbox 的部署里 reporter 没有 `session_factory`，`report()` 仍逐条直连 POST（现有测试与灰度部署不受影响）。

## 3. 链路

```text
UsageCollector（模型调用边界）
  → UsageReporter.report(account_id, run_id, samples)
      ├─ 有 session_factory：OutboxRepository.record("usage.report.v1", payload)  [独立短事务]
      └─ 否则：逐条 POST /v1/usage/events（旧路径）
  → OutboxDispatcher.drain_once()  [进程内总线]
  → UsageDeliveryConsumer
      ├─ is_processed(usage-delivery, envelope.id) → 跳过
      ├─ 逐条 POST /v1/usage/events  body = {**sample, run_id, event_id}
      ├─ 全部成功 → mark_processed
      └─ 任一条失败 → raise UsageDeliveryError → mark_failed(退避重试)
  → account POST /v1/usage/events → record_usage() 按 event_id 幂等落库
```

- 事件类型：`usage.report.v1`；payload 为 `{"run_id": str | null, "samples": [{model, prompt_tokens, completion_tokens, cost_usd, billing_source}]}`。
- 消费者名：`usage-delivery`（`processed_events.consumer`）。
- 一条 outbox 事件对应一次上报调用（可能含多个样本，例如整张 approve 图共用一个采集器）。

## 4. 装配与开关

- `services/usage.py`：`UsageReporter.attach_session_factory()` / `durable`；`_enqueue()` 写 outbox；`deliver()` 是严格版单条 POST（失败抛 `UsageDeliveryError` 供 dispatcher 退避）。
- `services/outbox.py`：`register_consumers(..., usage=reporter)` 订阅 `usage.report.v1`。
- `core/container_outbox.py`：`usage_outbox_enabled and reporter() is not None` 时挂 `session_factory`，并在 `outbox_enabled or durable_usage` 时启动 dispatcher——只跑用量投递也需要 dispatcher。
- `Settings.usage_outbox_enabled`（默认 `True`，`core/config.py`）：关掉退回直连；与 `outbox_enabled` 独立（后者控制 run 事件 outbox 与知识入库消费者）。
- 测试：`common.service_testkit.settings.NETWORK_SWITCHES` 钉 `False`，既有直连断言不变；覆盖用例显式传 `usage_outbox_enabled=True`。

## 5. account 侧幂等

- 表：`account/models/account.py` 的 `UsageEvent` 加 `event_id String(64) NULL` + `UniqueConstraint(account_id, event_id)`（命名约定 `uq_usage_events_account_id`）；迁移 `migrations/versions/a9d4f2c8b6e1_usage_event_id.py`（SQLite 下加列与加约束分两个 batch）。
- 写入：`UsageEventCreate.event_id`（≤64，可空）；`services/insights.py` 的 `record_usage()` 先 `find_by_event_id()` 命中即返回已有行；未命中在 `session.begin_nested()` 里插入，`IntegrityError` 再查一次（并发重投安全）。
- `event_id` 为空时沿用旧语义（每条都落库）；唯一约束含 `account_id`，账号间互不影响。
- 端点契约不变：`POST /v1/usage/events` 201 `{"id": ...}`，重复投递返回同一 id。

## 6. 失败语义

| 失败点                                | 行为                                                                                                                                                    |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| outbox 入队异常                       | warning `usage_enqueue_failed` + `usage_report` 指标，退回逐条直连                                                                                      |
| 投递 HTTP 非 2xx / 网络异常           | `UsageDeliveryError` → dispatcher `mark_failed`，按 `DEFAULT_BACKOFF_SECONDS=(1,5,30,120)` 退避，超过 `outbox_max_attempts` 落 `failed` 留 `last_error` |
| 事件结构非法（缺 account_id/samples） | 抛 `ValueError`，dispatcher 记 failed，不重试                                                                                                           |
| 重复投递                              | consumer 命中 `processed_events` 跳过；即使绕过也在 account 唯一约束兜底                                                                                |

- 未投递成功的事件可用现有运维面 `/api/v1/outbox/*`（列表/计数/重放）看见并人工重放。
- 投递是「至少一次」：极端情况下同一样本可能被 POST 多次，account 幂等保证只计一次。

## 7. 残余 / 后续

- **单实例语义**：outbox claim 仍无 `FOR UPDATE SKIP LOCKED`、无租约，dispatcher 走进程内 `EventBus`；多副本部署前需先做 P0-4（outbox claim 锁 + 跨实例传输，如 Redis Streams）。目前多副本会重复 claim 事件，靠 account 幂等兜底不重复计费，但会浪费投递。
- 事件只会被 `usage-delivery` 一个消费者消费；若以后出现第二个消费者，需按 consumer 名分别幂等（`processed_events` 已按 consumer 隔离）。
- account 侧未做用量事件对账/告警（`failed` 事件的告警属运维面，可接 P1-K 成本看板）。

## 8. 验证

- 编排器：`npx nx test agent-orchestrator`——`test_usage.py::test_usage_report_is_durable_through_outbox` 覆盖「只写 outbox 不直连 → 后台投递 → event_id 形状 → 重投不重复 POST」。
- 账号：`npx nx test account-service`——`test_usage_event_id_is_idempotent` / `test_usage_event_id_optional_and_bounded`；服务目录 `uv run alembic check`。
- 全链路手工：本地 `npm run dev` 跑一个 run 后查 `outbox_events`（status=dispatched）与 `usage_events.event_id`。
