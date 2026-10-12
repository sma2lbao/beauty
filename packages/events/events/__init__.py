"""beauty-moon 事件共享库。

内容:

- `events.envelope`:`EventEnvelope` 与事件类型命名/版本约定;
- `events.bus`:`EventBus` 抽象 + 进程内实现(`InMemoryEventBus`);
- `events.outbox`:事务性 outbox(模型/仓储/dispatcher)与消费幂等 `mark_processed`。

后续按 docs/architecture.md 补齐:Redis Streams 总线适配(阶段 B)。
"""

from events.bus import ALL, EventBus, Handler, HandlerError, InMemoryEventBus
from events.envelope import EventEnvelope, EventTypeError
from events.outbox import (
    DISPATCHED,
    FAILED,
    PENDING,
    DispatchStats,
    OutboxDispatcher,
    OutboxEvent,
    OutboxRepository,
    ProcessedEvent,
    mark_processed,
)

__all__ = [
    "ALL",
    "DISPATCHED",
    "FAILED",
    "PENDING",
    "DispatchStats",
    "EventBus",
    "EventEnvelope",
    "EventTypeError",
    "Handler",
    "HandlerError",
    "InMemoryEventBus",
    "OutboxDispatcher",
    "OutboxEvent",
    "OutboxRepository",
    "ProcessedEvent",
    "mark_processed",
]
