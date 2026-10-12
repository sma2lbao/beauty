"""进程内事件总线:前缀/通配匹配、订阅顺序、错误隔离与聚合报错。"""

from __future__ import annotations

import pytest

from events.bus import ALL, HandlerError, InMemoryEventBus, _matches
from events.envelope import EventEnvelope


def _event(type_: str = "run.delivered.v1") -> EventEnvelope:
    return EventEnvelope.create(type_, {"n": 1})


def test_pattern_matching() -> None:
    assert _matches("run", "run.delivered.v1")  # 前缀
    assert _matches("run.*", "run.delivered.v1")  # 通配段
    assert _matches("run.delivered.v1", "run.delivered.v1")  # 精确
    assert _matches(ALL, "anything.happened.v1")  # 全订阅
    assert not _matches("run.delivered.v1", "run.delivered.v2")  # 版本不同
    assert not _matches("run.delivered.v1.extra", "run.delivered.v1")  # 模式更长
    assert not _matches("email", "run.delivered.v1")


async def test_publish_delivers_in_subscription_order() -> None:
    bus = InMemoryEventBus()
    seen: list[tuple[str, str]] = []

    async def first(envelope: EventEnvelope) -> None:
        seen.append(("first", envelope.id))

    async def second(envelope: EventEnvelope) -> None:
        seen.append(("second", envelope.id))

    bus.subscribe("run", first)
    bus.subscribe(ALL, second)
    event = _event()

    assert await bus.publish(event) == 2
    assert seen == [("first", event.id), ("second", event.id)]
    assert bus.subscription_count == 2


async def test_non_matching_handler_is_skipped() -> None:
    bus = InMemoryEventBus()
    seen: list[str] = []

    async def email_handler(envelope: EventEnvelope) -> None:
        seen.append(envelope.type)

    bus.subscribe("email.received.v1", email_handler)

    assert await bus.publish(_event("run.completed.v1")) == 0
    assert seen == []


async def test_publish_without_subscribers_returns_zero() -> None:
    assert await InMemoryEventBus().publish(_event()) == 0


async def test_failing_handler_is_isolated_and_reported() -> None:
    """一个处理器炸了,其它照常收到;但 publish 抛 HandlerError 让 outbox 重试。"""
    bus = InMemoryEventBus()
    delivered: list[str] = []

    async def good(envelope: EventEnvelope) -> None:
        delivered.append(envelope.id)

    async def bad(envelope: EventEnvelope) -> None:
        raise RuntimeError("处理器挂了")

    bus.subscribe("run", good)
    bus.subscribe(ALL, bad)
    event = _event()

    with pytest.raises(HandlerError) as exc_info:
        await bus.publish(event)

    assert exc_info.value.attempted == 2
    assert exc_info.value.succeeded == 1
    assert "处理器挂了" in exc_info.value.failures[0]
    assert delivered == [event.id]  # 失败不影响已成功的处理器


async def test_failure_report_names_callable_object_handlers() -> None:
    """可调用对象(如消费者类实例)的失败原因要用类名,而不是 <object at 0x…>。"""

    class EmailConsumer:
        async def __call__(self, envelope: EventEnvelope) -> None:
            raise RuntimeError("消费者挂了")

    bus = InMemoryEventBus()
    bus.subscribe("email.received.v1", EmailConsumer())

    with pytest.raises(HandlerError) as exc_info:
        await bus.publish(_event("email.received.v1"))

    failure = exc_info.value.failures[0]
    assert failure.startswith(f"{EmailConsumer.__qualname__}:")
    assert "消费者挂了" in failure
    assert "object at 0x" not in failure
