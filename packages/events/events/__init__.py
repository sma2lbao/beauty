"""beauty-moon 事件共享库。

当前内容:事件信封(命名与版本约定)。后续按 docs/architecture.md 补齐:
事件总线抽象(Redis Streams 适配)、outbox 助手、消费幂等夹具。
"""

from events.envelope import EventEnvelope, EventTypeError

__all__ = ["EventEnvelope", "EventTypeError"]
