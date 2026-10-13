"""事务性 outbox:同事务落库、投递标记、退避重试、消费幂等。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import select

from events.bus import EventBus, InMemoryEventBus
from events.envelope import EventEnvelope
from events.outbox import (
    DISPATCHED,
    FAILED,
    PENDING,
    OutboxDispatcher,
    OutboxRepository,
    ProcessedEvent,
    is_processed,
    mark_processed,
)


class RecordingBus:
    """记录收到的事件;可注入失败前 N 次(模拟消费者临时不可用)。"""

    def __init__(self, fail_times: int = 0) -> None:
        self.received: list[EventEnvelope] = []
        self.fail_times = fail_times
        self.calls = 0

    async def publish(self, envelope: EventEnvelope) -> int:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("消费者不可用")
        self.received.append(envelope)
        return 1

    def subscribe(self, pattern: str, handler) -> None:  # pragma: no cover - 协议占位
        raise NotImplementedError


@pytest.fixture()
async def factory(tmp_path) -> AsyncIterator:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/outbox.db")
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await factory.engine.dispose()


def _envelope(type_: str = "run.delivered.v1") -> EventEnvelope:
    return EventEnvelope.create(type_, {"title": "周报"})


async def test_rolled_back_transaction_leaves_no_event(factory) -> None:
    """核心性质:事务回滚时事件也一起回滚——不会出现"状态没改但事件发了"。"""

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        async with session_scope(factory) as session:
            await OutboxRepository(session).record("t-1", _envelope())
            raise Boom("业务失败")

    async with session_scope(factory) as session:
        assert await OutboxRepository(session).count_by_status(PENDING) == 0


async def test_claim_respects_available_at(factory) -> None:
    base = datetime.now(UTC)
    async with session_scope(factory) as session:
        repository = OutboxRepository(session)
        await repository.record(
            "t-1", _envelope(), available_at=base + timedelta(minutes=5)
        )
        await repository.record(
            "t-1",
            _envelope("run.completed.v1"),
            available_at=base - timedelta(minutes=1),
        )

    async with session_scope(factory) as session:
        repository = OutboxRepository(session)
        claimed = await repository.claim(now=base)
        assert [event.event_type for event in claimed] == ["run.completed.v1"]
        # 到期后两条都在
        later = await repository.claim(now=base + timedelta(minutes=6))
        assert len(later) == 2


async def test_dispatcher_publishes_and_marks_dispatched(factory) -> None:
    bus = RecordingBus()
    async with session_scope(factory) as session:
        event = await OutboxRepository(session).record("t-1", _envelope())

    stats = await OutboxDispatcher(factory, bus).drain_once()

    assert (stats.claimed, stats.delivered, stats.failed) == (1, 1, 0)
    assert [received.id for received in bus.received] == [event.id]
    assert bus.received[0].payload == {"title": "周报"}  # 载荷原样传递

    async with session_scope(factory) as session:
        repository = OutboxRepository(session)
        assert await repository.count_by_status(PENDING) == 0
        assert await repository.count_by_status(DISPATCHED) == 1


async def test_failure_retries_with_backoff_then_succeeds(factory) -> None:
    bus = RecordingBus(fail_times=1)
    dispatcher = OutboxDispatcher(
        factory, bus, max_attempts=5, backoff_seconds=(0.0, 0.0)
    )
    async with session_scope(factory) as session:
        recorded = await OutboxRepository(session).record("t-1", _envelope())

    first = await dispatcher.drain_once()
    assert (first.claimed, first.delivered, first.failed) == (1, 0, 1)

    async with session_scope(factory) as session:
        repository = OutboxRepository(session)
        assert await repository.count_by_status(PENDING) == 1  # 仍在待投递
        pending = await repository.claim()
        assert pending[0].attempts == 1
        assert "消费者不可用" in (pending[0].last_error or "")
    assert recorded.id

    second = await dispatcher.drain_once()  # 退避为 0,立即可重试
    assert (second.delivered, second.failed) == (1, 0)
    async with session_scope(factory) as session:
        assert await OutboxRepository(session).count_by_status(DISPATCHED) == 1


async def test_max_attempts_marks_failed_and_stops_retrying(factory) -> None:
    bus = RecordingBus(fail_times=99)
    dispatcher = OutboxDispatcher(factory, bus, max_attempts=2, backoff_seconds=(0.0,))
    async with session_scope(factory) as session:
        await OutboxRepository(session).record("t-1", _envelope())

    assert (await dispatcher.drain_once()).failed == 1
    assert (await dispatcher.drain_once()).failed == 1

    async with session_scope(factory) as session:
        repository = OutboxRepository(session)
        assert await repository.count_by_status(FAILED) == 1
        assert await repository.count_by_status(PENDING) == 0

    # 落 failed 后不再重试(可查、可人工重放,但不静默重投)
    assert (await dispatcher.drain_once()).claimed == 0


async def test_drain_empty_returns_zero(factory) -> None:
    stats = await OutboxDispatcher(factory, RecordingBus()).drain_once()
    assert (stats.claimed, stats.delivered, stats.failed) == (0, 0, 0)


async def test_run_forever_drains_until_stop(factory) -> None:
    bus = RecordingBus()
    dispatcher = OutboxDispatcher(factory, bus)
    async with session_scope(factory) as session:
        await OutboxRepository(session).record("t-1", _envelope())

    stop = asyncio.Event()
    task = asyncio.create_task(dispatcher.run_forever(interval_seconds=0.01, stop=stop))
    for _ in range(50):
        if bus.received:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert len(bus.received) == 1
    assert task.done()


async def test_mark_processed_is_idempotent_per_consumer(factory) -> None:
    async with session_scope(factory) as session:
        assert await mark_processed(
            session, tenant_id="t-1", consumer="knowledge-ingest", event_id="e1"
        )
        # 同一消费者重复事件 → False(跳过)
        assert not await mark_processed(
            session, tenant_id="t-1", consumer="knowledge-ingest", event_id="e1"
        )
        # 不同消费者/不同租户各自独立
        assert await mark_processed(
            session, tenant_id="t-1", consumer="audit", event_id="e1"
        )
        assert await mark_processed(
            session, tenant_id="t-2", consumer="knowledge-ingest", event_id="e1"
        )


async def test_mark_processed_does_not_poison_outer_transaction(factory) -> None:
    """重复判定用 SAVEPOINT 兜住:外层事务仍可继续写自己的状态。"""
    async with session_scope(factory) as session:
        assert await mark_processed(
            session, tenant_id="t-1", consumer="c", event_id="e1"
        )
        assert not await mark_processed(
            session, tenant_id="t-1", consumer="c", event_id="e1"
        )
        # 冲突之后仍能正常写入并提交
        await OutboxRepository(session).record("t-1", _envelope())

    async with session_scope(factory) as session:
        assert await OutboxRepository(session).count_by_status(PENDING) == 1


def test_buses_satisfy_event_bus_protocol() -> None:
    """守住:测试替身与真实总线都满足 EventBus 契约(换 Redis 适配时同理)。"""
    assert isinstance(InMemoryEventBus(), EventBus)
    assert isinstance(RecordingBus(), EventBus)


async def test_handler_runs_outside_outbox_transaction(factory) -> None:
    """回归:处理器在 outbox 事务**外**运行——它写同一个库不该被锁住。

    老实现把 `bus.publish` 放在 drain 的单个事务里,处理器(会调外部服务、
    写自己的库)于是撞上 `database is locked`/长事务持锁。三段式后
    "处理器写同一个库"必须成功。
    """

    class WritingBus:
        def __init__(self, session_factory) -> None:
            self._factory = session_factory
            self.written: list[str] = []

        async def publish(self, envelope: EventEnvelope) -> int:
            # 处理器自己的库写入:走独立事务
            async with session_scope(self._factory) as session:
                await mark_processed(
                    session,
                    tenant_id=envelope.tenant_id or "t-1",
                    consumer="writing-consumer",
                    event_id=envelope.id,
                )
            self.written.append(envelope.id)
            return 1

        def subscribe(self, pattern: str, handler) -> None:  # pragma: no cover
            raise NotImplementedError

    bus = WritingBus(factory)
    async with session_scope(factory) as session:
        recorded = await OutboxRepository(session).record("t-1", _envelope())

    stats = await OutboxDispatcher(factory, bus).drain_once()

    assert (stats.claimed, stats.delivered, stats.failed) == (1, 1, 0)
    assert bus.written == [recorded.id]
    async with session_scope(factory) as session:
        rows = list(
            await session.scalars(
                select(ProcessedEvent).where(ProcessedEvent.event_id == recorded.id)
            )
        )
        assert len(rows) == 1  # 处理器的写入确实落库了


async def test_is_processed_matches_mark_processed(factory) -> None:
    """查与标记配对:未处理 → False;标记后 → True;别的消费者不受影响。"""
    async with session_scope(factory) as session:
        assert not await is_processed(
            session, tenant_id="t-1", consumer="c", event_id="e9"
        )
        await mark_processed(session, tenant_id="t-1", consumer="c", event_id="e9")
        assert await is_processed(session, tenant_id="t-1", consumer="c", event_id="e9")
        assert not await is_processed(
            session, tenant_id="t-1", consumer="other", event_id="e9"
        )


async def test_ops_list_stats_and_requeue(factory) -> None:
    """运维面:按状态列事件、计数、单个/批量重放(重放后 attempts 清零)。"""
    async with session_scope(factory) as session:
        repository = OutboxRepository(session)
        ok = await repository.record("t-1", _envelope("run.completed.v1"))
        await repository.mark_dispatched(ok)
        broken = await repository.record("t-1", _envelope("run.delivered.v1"))
        await repository.mark_failed(broken, "消费者挂了", max_attempts=1)
        await repository.record("t-2", _envelope("run.delivered.v1"))

    async with session_scope(factory) as session:
        repository = OutboxRepository(session)
        # 计数(可全局也可按租户)
        assert await repository.stats() == {PENDING: 1, DISPATCHED: 1, FAILED: 1}
        tenant_stats = await repository.stats(tenant_id="t-1")
        assert tenant_stats[FAILED] == 1
        assert tenant_stats[PENDING] == 0

        # 列事件:按状态 + 按租户
        failed = await repository.list_events(status=FAILED)
        assert [event.id for event in failed] == [broken.id]
        assert await repository.list_events(tenant_id="t-2") != []

        # 单个重放:回 pending、attempts 清零、保留上次原因
        await repository.requeue(failed[0])
        assert failed[0].status == PENDING
        assert failed[0].attempts == 0
        assert failed[0].last_error == "消费者挂了"  # 原样保留上次失败原因

    async with session_scope(factory) as session:
        repository = OutboxRepository(session)
        # 批量重放只影响 failed
        assert await repository.requeue_failed() == 0  # 已无 failed
        second = await repository.get_by_id(broken.id)
        assert second is not None
        await repository.mark_failed(second, "又挂了", max_attempts=1)
        assert await repository.requeue_failed() == 1
        assert await repository.stats() == {PENDING: 2, DISPATCHED: 1, FAILED: 0}
