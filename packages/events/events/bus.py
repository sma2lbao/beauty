"""事件总线:进程内实现 + 抽象(阶段 B 换 Redis Streams 适配)。

约定:

- 订阅用**类型前缀 + `*` 通配**:`run` 匹配所有 run 事件,`run.*` 匹配
  `run.delivered.v1` 这类两段以上,`#` 匹配全部;版本段就是普通段,
  所以想只收 v1 就写 `run.delivered.v1`;
- 一个处理器失败**不影响**其它处理器,但 `publish` 会在全部投递尝试后抛
  `HandlerError`——outbox 据此判定"这次投递失败",留待退避重试;
- 因此投递语义是**至少一次**:重试会再次投给已成功的处理器,消费方需要幂等
  (用 `events.outbox.mark_processed` 做去重)。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from common.logging import get_logger

from events.envelope import EventEnvelope

Handler = Callable[[EventEnvelope], Awaitable[None]]
ALL = "#"


class HandlerError(RuntimeError):
    """一个或多个处理器投递失败(聚合报告,便于 outbox 记录原因)。"""

    def __init__(self, attempted: int, succeeded: int, failures: list[str]) -> None:
        self.attempted = attempted
        self.succeeded = succeeded
        self.failures = failures
        super().__init__(
            f"{attempted} 个处理器中 {len(failures)} 个失败:{'; '.join(failures[:3])}"
        )


@runtime_checkable
class EventBus(Protocol):
    """事件总线:发布信封并返回成功投递的处理器数。"""

    async def publish(self, envelope: EventEnvelope) -> int: ...

    def subscribe(self, pattern: str, handler: Handler) -> None: ...


def _handler_name(handler: Handler) -> str:
    """处理器名字:函数取 __qualname__,可调用对象取类名。

    直接 `repr(handler)` 会给出 `<... object at 0x…>`,对运维毫无价值——
    失败原因里必须能看出"是哪个消费者挂了"。
    """
    name = getattr(handler, "__qualname__", None)
    if name:
        return str(name)
    return type(handler).__qualname__


def _matches(pattern: str, event_type: str) -> bool:
    """前缀 + 通配匹配:`run` / `run.*` / `run.delivered.v1` / `#`。"""
    if pattern == ALL:
        return True
    pattern_parts = pattern.split(".")
    event_parts = event_type.split(".")
    if len(pattern_parts) > len(event_parts):
        return False
    return all(
        part == "*" or part == event_part
        for part, event_part in zip(pattern_parts, event_parts, strict=False)
    )


class InMemoryEventBus:
    """进程内总线:开发/测试与同进程消费者用(跨进程要 Redis Streams)。"""

    def __init__(self) -> None:
        self._subscriptions: list[tuple[str, Handler]] = []
        self._log = get_logger("events.bus")

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subscriptions.append((pattern, handler))

    @property
    def subscription_count(self) -> int:
        return len(self._subscriptions)

    async def publish(self, envelope: EventEnvelope) -> int:
        """投给所有匹配的处理器(按订阅顺序);有失败则抛 HandlerError。"""
        targets = [
            handler
            for pattern, handler in self._subscriptions
            if _matches(pattern, envelope.type)
        ]
        succeeded = 0
        failures: list[str] = []
        for handler in targets:
            try:
                await handler(envelope)
            except Exception as exc:  # 单点失败不影响其它处理器
                name = _handler_name(handler)
                self._log.warning(
                    "event_handler_failed",
                    event_type=envelope.type,
                    event_id=envelope.id,
                    handler=name,
                    error=f"{type(exc).__name__}: {exc}",
                )
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
            else:
                succeeded += 1
        if failures:
            raise HandlerError(len(targets), succeeded, failures)
        return succeeded
