# events

beauty-moon 事件共享库:信封约定 + 进程内总线 + 事务性 outbox + 消费幂等。

| 模块 | 内容 |
| --- | --- |
| `events.envelope` | `EventEnvelope`(id/type/occurred_at/payload/tenant_id)与类型命名约定 |
| `events.bus` | `EventBus` 抽象 + `InMemoryEventBus`(前缀/通配订阅、错误隔离) |
| `events.outbox` | `OutboxEvent`/`OutboxRepository`/`OutboxDispatcher` + `mark_processed` |

## 事件类型

`<域>.<实体>.<动作>.v<版本>`,如 `run.delivered.v1`、`email.received.v1`。
版本只增不改:破坏性变更发布 v2,消费方各自迁移。

## 订阅匹配

前缀 + `*` 通配:`run` 匹配所有 run 事件;`run.*` 匹配 `run.delivered.v1`;
`run.delivered.v1` 精确到版本;`#` 全订阅。

## 事务性 outbox(为什么这么写)

直接发事件会遇到两种不一致:*状态改了但事件丢了*、*事件发了但事务回滚了*。
outbox 把待发事件写进**与业务状态同一事务**的表,再由 dispatcher 投递:

```
业务写 + outbox 记录 ──commit──> dispatcher 轮询 ──> 总线 ──> 消费者
                      └─rollback─> 事件也不存在(不会发出去)
```

- **至少一次**:投递成功才标 `dispatched`;进程在"投递后、标记前"崩溃会重投,
  所以消费方必须幂等——`mark_processed(session, tenant_id=…, consumer=…, event_id=…)`
  返回 `False` 表示重复(内部用 SAVEPOINT,不会污染外层事务);
- **失败退避**:按 `backoff_seconds` 档位重排 `available_at`,超 `max_attempts`
  落 `failed`——不静默丢弃,留在表里可查、可人工重放;
- dispatcher 一轮一个事务:中途崩溃整批回滚,下轮重投。

## 写一个消费者

```python
async def on_delivered(envelope: EventEnvelope) -> None:
    if not await mark_processed(session, tenant_id=envelope.tenant_id,
                                consumer="my-consumer", event_id=envelope.id):
        return  # 重投,跳过
    ...  # 做你的活;抛异常即交给 outbox 退避重试

bus.subscribe("run.delivered.v1", on_delivered)
```

## 阶段进度

已完成:信封(含租户)、进程内总线、outbox 模型/仓储/dispatcher、消费幂等。
阶段 B:Redis Streams 总线适配(换 `EventBus` 实现与触发方式,契约不变)、
outbox 清理/归档任务。
