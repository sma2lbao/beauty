"""持久 shell 会话目录:所有权登记、租约续期与孤儿回收。

跨实例共享的唯一真源是一张表(`shell_sessions`);输出 ring buffer 与订阅者
仍只在持有实例的内存里。因此:

- 同一会话的所有权由 `owner_instance` + 租约决定,续租成功才代表持有实例还活着;
- 契约明确:非持有实例收到 `sandbox.session_elsewhere`(可接管时带上
  `resumable` 与 `owner_instance`),而不是含糊的 404;
- 持有实例崩溃后租约到期,sweeper 把行标 `lost`,并在 provider 支持 attach 时
  先尽力结束孤儿 pty,避免沙箱里堆僵尸 shell。
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from database.session import session_scope
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workspace.core.config import Settings
from workspace.models.shell import ShellSessionRecord

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """SQLite 存回来是不带时区的 datetime;统一按 UTC 解释。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def resolve_instance_id(settings: Settings) -> str:
    """实例身份:显式配置优先,否则 hostname:pid(同机多进程算不同实例)。

    默认带 pid 是有意的:同一台机器上跑两个进程(uvicorn 多 worker、蓝绿并存)
    时,它们不应该同时认为自己持有同一个 pty。
    """
    configured = (settings.instance_id or "").strip()
    if configured:
        return configured
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True)
class ShellSessionClaim:
    """登记表里一行会话的所有权快照(不带动 ORM 对象)。"""

    account_id: str
    sandbox_id: str
    session_id: str
    provider: str
    runtime_name: str
    provider_pid: int | None
    cwd: str
    cols: int
    rows: int
    tty: bool
    status: str
    owner_instance: str
    created_at: datetime
    lease_expires_at: datetime

    def lease_expired(self, now: datetime | None = None) -> bool:
        """租约是否已过期(持有实例不再续租 = 它已经不在了)。"""
        return self.lease_expires_at <= (_utcnow() if now is None else now)


class ShellSessionRegistry:
    """登记表的读写:开会话时登记、每轮 sweep 续租、失联后回收。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        instance_id: str,
        lease_seconds: float = 120.0,
    ) -> None:
        self._factory = factory
        self.instance_id = instance_id
        self._lease = timedelta(seconds=lease_seconds)

    async def register(
        self,
        *,
        account_id: str,
        sandbox_id: str,
        session_id: str,
        provider: str,
        runtime_name: str,
        provider_pid: int | None,
        cwd: str,
        cols: int,
        rows: int,
        tty: bool,
        created_at: datetime,
    ) -> None:
        """登记一个新会话(本实例持有)。"""
        now = _utcnow()
        async with session_scope(self._factory) as session:
            session.add(
                ShellSessionRecord(
                    account_id=account_id,
                    sandbox_id=sandbox_id,
                    session_id=session_id,
                    provider=provider,
                    runtime_name=runtime_name,
                    provider_pid=provider_pid,
                    cwd=cwd,
                    cols=cols,
                    rows=rows,
                    tty=tty,
                    status="running",
                    owner_instance=self.instance_id,
                    lease_expires_at=now + self._lease,
                    last_activity_at=now,
                    created_at=created_at,
                )
            )

    async def lookup(
        self, account_id: str, sandbox_id: str, session_id: str
    ) -> ShellSessionClaim | None:
        """按 (账号, 沙箱, 会话) 取所有权快照。"""
        async with session_scope(self._factory) as session:
            record = await self._find(session, account_id, sandbox_id, session_id)
            return None if record is None else _claim(record)

    async def count_active(self, account_id: str, sandbox_id: str) -> int:
        """该沙箱里租约仍有效的活跃会话数(跨实例)。"""
        async with session_scope(self._factory) as session:
            statement = (
                select(func.count())
                .select_from(ShellSessionRecord)
                .where(
                    ShellSessionRecord.account_id == account_id,
                    ShellSessionRecord.sandbox_id == sandbox_id,
                    ShellSessionRecord.status == "running",
                    ShellSessionRecord.lease_expires_at > _utcnow(),
                )
            )
            return int(await session.scalar(statement) or 0)

    async def touch(self, keys: list[tuple[str, str, str]]) -> int:
        """续租本实例持有的会话;返回续上的行数。"""
        if not keys:
            return 0
        now = _utcnow()
        touched = 0
        async with session_scope(self._factory) as session:
            for account_id, sandbox_id, session_id in keys:
                record = await self._find(
                    session, account_id, sandbox_id, session_id, owner=self.instance_id
                )
                if record is None or record.status != "running":
                    continue
                record.lease_expires_at = now + self._lease
                record.last_activity_at = now
                touched += 1
        return touched

    async def adopt(self, claim: ShellSessionClaim) -> bool:
        """原子接管一行:所有者改成自己并续租。

        单条 `UPDATE ... WHERE status='running' AND (租约已过期 OR owner 是自己)`,
        两个实例同时接管时只有一个能改到行(另一个 rowcount=0),不会出现
        "都以为自己持有、各读一半输出"的分裂。
        """
        now = _utcnow()
        async with session_scope(self._factory) as session:
            result = await session.execute(
                update(ShellSessionRecord)
                .where(
                    ShellSessionRecord.account_id == claim.account_id,
                    ShellSessionRecord.sandbox_id == claim.sandbox_id,
                    ShellSessionRecord.session_id == claim.session_id,
                    ShellSessionRecord.status == "running",
                    or_(
                        ShellSessionRecord.lease_expires_at <= now,
                        ShellSessionRecord.owner_instance == self.instance_id,
                    ),
                )
                .values(
                    owner_instance=self.instance_id,
                    lease_expires_at=now + self._lease,
                    last_activity_at=now,
                )
            )
            if not result.rowcount:
                return False
        logger.info(
            "shell_session.adopted",
            account_id=claim.account_id,
            sandbox_id=claim.sandbox_id,
            session_id=claim.session_id,
            previous_owner=claim.owner_instance,
            instance_id=self.instance_id,
        )
        return True

    async def mark_closed(
        self,
        keys: list[tuple[str, str, str]],
        *,
        exit_code: int | None = None,
    ) -> int:
        """把会话标成正常结束(幂等)。"""
        if not keys:
            return 0
        now = _utcnow()
        closed = 0
        async with session_scope(self._factory) as session:
            for account_id, sandbox_id, session_id in keys:
                record = await self._find(session, account_id, sandbox_id, session_id)
                if record is None or record.status != "running":
                    continue
                record.status = "closed"
                record.closed_at = now
                record.exit_code = exit_code
                closed += 1
        return closed

    async def mark_lost(self, claim: ShellSessionClaim, *, reason: str) -> None:
        """把会话标成失联(持有实例消失且无法接管)。"""
        async with session_scope(self._factory) as session:
            record = await self._find(
                session, claim.account_id, claim.sandbox_id, claim.session_id
            )
            if record is None or record.status != "running":
                return
            record.status = "lost"
            record.closed_at = _utcnow()
        logger.warning(
            "shell_session.lost",
            account_id=claim.account_id,
            sandbox_id=claim.sandbox_id,
            session_id=claim.session_id,
            previous_owner=claim.owner_instance,
            reason=reason,
        )

    async def close_sandbox(self, account_id: str, sandbox_id: str) -> int:
        """沙箱被销毁/过期:把该沙箱下所有仍标记运行的会话标成结束。"""
        now = _utcnow()
        closed = 0
        async with session_scope(self._factory) as session:
            records = list(
                await session.scalars(
                    select(ShellSessionRecord).where(
                        ShellSessionRecord.account_id == account_id,
                        ShellSessionRecord.sandbox_id == sandbox_id,
                        ShellSessionRecord.status == "running",
                    )
                )
            )
            for record in records:
                record.status = "closed"
                record.closed_at = now
                closed += 1
        return closed

    async def list_stale(
        self, *, limit: int = 50, now: datetime | None = None
    ) -> list[ShellSessionClaim]:
        """租约已过期的运行中会话(持有实例已经不在了)。"""
        moment = _utcnow() if now is None else now
        async with session_scope(self._factory) as session:
            records = list(
                await session.scalars(
                    select(ShellSessionRecord)
                    .where(
                        ShellSessionRecord.status == "running",
                        ShellSessionRecord.lease_expires_at <= moment,
                    )
                    .order_by(ShellSessionRecord.lease_expires_at)
                    .limit(limit)
                )
            )
            return [_claim(record) for record in records]

    async def purge_closed(self, *, retention_seconds: float) -> int:
        """删掉过了保留期的已结束/失联行(只做记录清理,不碰运行时)。"""
        cutoff = _utcnow() - timedelta(seconds=retention_seconds)
        async with session_scope(self._factory) as session:
            result = await session.execute(
                delete(ShellSessionRecord).where(
                    ShellSessionRecord.status != "running",
                    ShellSessionRecord.closed_at.is_not(None),
                    ShellSessionRecord.closed_at < cutoff,
                )
            )
            return int(result.rowcount or 0)

    @staticmethod
    async def _find(
        session: AsyncSession,
        account_id: str,
        sandbox_id: str,
        session_id: str,
        *,
        owner: str | None = None,
    ) -> ShellSessionRecord | None:
        statement = select(ShellSessionRecord).where(
            ShellSessionRecord.account_id == account_id,
            ShellSessionRecord.sandbox_id == sandbox_id,
            ShellSessionRecord.session_id == session_id,
        )
        if owner is not None:
            statement = statement.where(ShellSessionRecord.owner_instance == owner)
        return await session.scalar(statement)


def _claim(record: ShellSessionRecord) -> ShellSessionClaim:
    return ShellSessionClaim(
        account_id=record.account_id,
        sandbox_id=record.sandbox_id,
        session_id=record.session_id,
        provider=record.provider,
        runtime_name=record.runtime_name,
        provider_pid=record.provider_pid,
        cwd=record.cwd,
        cols=record.cols,
        rows=record.rows,
        tty=bool(record.tty),
        status=record.status,
        owner_instance=record.owner_instance,
        created_at=_as_utc(record.created_at),
        lease_expires_at=_as_utc(record.lease_expires_at),
    )


def build_shell_session_registry(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> ShellSessionRegistry:
    """按配置装配登记表:实例身份 + 租约长度。"""
    return ShellSessionRegistry(
        factory,
        instance_id=resolve_instance_id(settings),
        lease_seconds=float(settings.sandbox_shell_lease_seconds),
    )
