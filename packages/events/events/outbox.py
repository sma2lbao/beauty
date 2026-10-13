"""事务性 outbox(发件箱):业务状态与待发事件**同事务**落库,再异步投递。

为什么需要:直接把事件发出去,会遇到"状态已改但事件丢了"或"事件发了但事务回滚"
两种不一致。outbox 把事件先写进**同一事务**的表,再由 dispatcher 投递——
要么两者都成功,要么两者都不发生。

语义与代价(明说):

- **至少一次**:投递成功才标记 dispatched;进程在投递后、标记前崩溃会导致重投,
  所以消费方必须幂等(用 `mark_processed` 去重);
- 失败退避重试(指数式档位),超过 `max_attempts` 落 `failed` 状态——
  不静默丢弃,留在表里可查可重放;
- dispatcher 用**三段式**(短事务领取 → 事务外投递 → 短事务标记):处理器
  会调外部服务/写自己的库,绝不能跑在 outbox 事务里(sqlite 下会 `database is locked`,
  其它库上也会造成长事务持锁);代价是两步之间崩溃会重投,而这本就是至少一次语义;
- 阶段 A 的 dispatcher 是进程内轮询;阶段 B 换 Redis Streams 时,
  `EventBus`/`OutboxDispatcher` 的契约不变(只换总线实现与触发方式)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from common.logging import get_logger
from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from events.bus import EventBus
from events.envelope import EventEnvelope

PENDING = "pending"
DISPATCHED = "dispatched"
FAILED = "failed"

DEFAULT_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 5.0, 30.0, 120.0)


class OutboxEvent(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """待投递事件(id 即信封 id,作为消费幂等的去重键)。"""

    __tablename__ = "outbox_events"

    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[str] = mapped_column(String(40))  # 信封里的发生时间(原样保留)
    # 事件里带的租户:消费方按租户写自己的库,不依赖外部上下文
    envelope_tenant_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_envelope(self) -> EventEnvelope:
        return EventEnvelope(
            id=self.id,
            type=self.event_type,
            occurred_at=self.occurred_at,
            payload=dict(self.payload or {}),
            tenant_id=self.envelope_tenant_id or self.tenant_id,
        )


class ProcessedEvent(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """消费幂等:同一 (租户, 消费者, 事件) 只处理一次。"""

    __tablename__ = "processed_events"
    __table_args__ = (UniqueConstraint("tenant_id", "consumer", "event_id"),)

    consumer: Mapped[str] = mapped_column(String(80))
    event_id: Mapped[str] = mapped_column(String(32), index=True)


class OutboxRepository:
    """outbox 的读写(故意不做成 TenantRepository:dispatcher 跨租户扫表)。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        tenant_id: str,
        envelope: EventEnvelope,
        *,
        available_at: datetime | None = None,
    ) -> OutboxEvent:
        """与业务状态同一事务登记事件;调用方随后提交,两者一起生效。"""
        event = OutboxEvent(
            id=envelope.id,
            tenant_id=tenant_id,
            event_type=envelope.type,
            payload=envelope.payload,
            occurred_at=envelope.occurred_at,
            envelope_tenant_id=envelope.tenant_id or tenant_id,
            available_at=available_at or datetime.now(UTC),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def claim(
        self, *, limit: int = 50, now: datetime | None = None
    ) -> list[OutboxEvent]:
        """取一批待投递事件(跨租户,按登记顺序)。"""
        moment = now or datetime.now(UTC)
        rows = await self.session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.status == PENDING,
                OutboxEvent.available_at <= moment,
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(limit)
        )
        return list(rows)

    async def get_by_id(self, event_id: str) -> OutboxEvent | None:
        """按 id 取事件(dispatcher 在独立事务里标记时用)。"""
        return await self.session.get(OutboxEvent, event_id)

    async def mark_dispatched(self, event: OutboxEvent) -> OutboxEvent:
        event.status = DISPATCHED
        event.dispatched_at = datetime.now(UTC)
        event.last_error = None
        await self.session.flush()
        return event

    async def mark_failed(
        self,
        event: OutboxEvent,
        error: str,
        *,
        max_attempts: int = 5,
        backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    ) -> OutboxEvent:
        """记一次失败:累加尝试数、写入原因,并按退避档位安排下次;超限落 failed。"""
        event.attempts += 1
        event.last_error = error[:500]
        if event.attempts >= max_attempts:
            event.status = FAILED
        else:
            index = min(event.attempts - 1, len(backoff_seconds) - 1)
            delay = backoff_seconds[index] if backoff_seconds else 0.0
            event.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        await self.session.flush()
        return event

    async def count_by_status(self, status: str) -> int:
        from sqlalchemy import func

        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.status == status)
            )
            or 0
        )

    # ---- 运维面:可见 + 可人工重放 ----

    async def list_events(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OutboxEvent]:
        """按租户/状态列出事件(运维排查用);最新在前。"""
        statement = select(OutboxEvent)
        if tenant_id is not None:
            statement = statement.where(OutboxEvent.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(OutboxEvent.status == status)
        statement = (
            statement.order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def stats(self, *, tenant_id: str | None = None) -> dict[str, int]:
        """各状态计数(控制台徽标/告警用)。"""
        from sqlalchemy import func

        statement = select(OutboxEvent.status, func.count()).group_by(
            OutboxEvent.status
        )
        if tenant_id is not None:
            statement = statement.where(OutboxEvent.tenant_id == tenant_id)
        rows = await self.session.execute(statement)
        counts = {PENDING: 0, DISPATCHED: 0, FAILED: 0}
        for status_value, count in rows.all():
            counts[str(status_value)] = int(count)
        return counts

    async def requeue(self, event: OutboxEvent) -> OutboxEvent:
        """人工重放:回到 pending 并清零退避/尝试数,立即可被下一轮领取。

        保留 `last_error`(重放成功时由 `mark_dispatched` 清掉),
        这样运维能看到"上次为什么失败",而不是重放后线索被抹掉。
        """
        event.status = PENDING
        event.attempts = 0
        event.available_at = datetime.now(UTC)
        event.dispatched_at = None
        await self.session.flush()
        return event

    async def requeue_failed(
        self, *, tenant_id: str | None = None, limit: int = 100
    ) -> int:
        """批量重放 failed 事件,返回重放条数。"""
        events = await self.list_events(tenant_id=tenant_id, status=FAILED, limit=limit)
        for event in events:
            await self.requeue(event)
        return len(events)


@dataclass(frozen=True, slots=True)
class DispatchStats:
    """一轮投递结果:claim 到多少、成功多少、失败多少。"""

    claimed: int
    delivered: int
    failed: int


class OutboxDispatcher:
    """把 outbox 里待投递的事件推给总线(至少一次、失败退避)。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        *,
        batch_size: int = 50,
        max_attempts: int = 5,
        backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        self._factory = factory
        self._bus = bus
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._log = get_logger("events.outbox")

    async def drain_once(self) -> DispatchStats:
        """投递一批:**三段式**,处理器绝不在数据库事务里运行。

        为什么不是"整批一个事务":处理器会调外部服务、写自己的库,把它放在
        outbox 事务里会导致①长事务持锁(sqlite 下直接 `database is locked`)、
        ②外部调用与数据库锁耦合。因此:

        1. 短事务:领取一批并提交(立刻释放锁);
        2. 事务外:逐条投递(失败只记录,不影响其它事件);
        3. 短事务:逐条标记 dispatched / failed(+ 退避)。

        代价:第 2、3 步之间崩溃会重投——这**本来**就是至少一次语义
        (消费方用 `mark_processed` 去重),所以没有变差。
        """
        from database.session import session_scope

        async with session_scope(self._factory) as session:
            claimed = await OutboxRepository(session).claim(limit=self._batch_size)
            envelopes = [(event.id, event.to_envelope()) for event in claimed]

        delivered = failed = 0
        for event_id, envelope in envelopes:
            error: str | None = None
            try:
                await self._bus.publish(envelope)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            # 每条一个短事务:成功/失败分别落库,互不牵连
            async with session_scope(self._factory) as session:
                repository = OutboxRepository(session)
                event = await repository.get_by_id(event_id)
                if event is None:  # 已被其它实例处理/清理
                    continue
                if error is None:
                    await repository.mark_dispatched(event)
                    delivered += 1
                else:
                    await repository.mark_failed(
                        event,
                        error,
                        max_attempts=self._max_attempts,
                        backoff_seconds=self._backoff_seconds,
                    )
                    failed += 1
        return DispatchStats(len(envelopes), delivered, failed)

    async def run_forever(
        self, *, interval_seconds: float, stop: asyncio.Event
    ) -> None:
        """后台轮询:直到 stop 被置位(测试与 lifespan 都用它)。"""
        while not stop.is_set():
            try:
                stats = await self.drain_once()
            except Exception as exc:  # 单轮失败不该终止循环
                self._log.warning(
                    "outbox_drain_failed", error=f"{type(exc).__name__}: {exc}"
                )
            else:
                if stats.claimed:
                    self._log.info(
                        "outbox_drained",
                        claimed=stats.claimed,
                        delivered=stats.delivered,
                        failed=stats.failed,
                    )
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue


async def is_processed(
    session: AsyncSession,
    *,
    tenant_id: str,
    consumer: str,
    event_id: str,
) -> bool:
    """该事件是否已被这个消费者处理过(与 `mark_processed` 配对,消费方先查后做)。"""
    found = await session.scalar(
        select(ProcessedEvent.id).where(
            ProcessedEvent.tenant_id == tenant_id,
            ProcessedEvent.consumer == consumer,
            ProcessedEvent.event_id == event_id,
        )
    )
    return found is not None


async def mark_processed(
    session: AsyncSession,
    *,
    tenant_id: str,
    consumer: str,
    event_id: str,
) -> bool:
    """消费去重:返回 True 表示首次处理,False 表示重复(调用方跳过)。

    用 SAVEPOINT 兜住唯一约束冲突,不会污染外层事务——消费方可以
    "先判重,再在同一事务里写自己的状态"。
    """
    try:
        async with session.begin_nested():
            session.add(
                ProcessedEvent(
                    tenant_id=tenant_id, consumer=consumer, event_id=event_id
                )
            )
            await session.flush()
    except IntegrityError:
        return False
    return True
