"""持久 shell 会话登记表:租约、原子接管、失联与回收。

零网络:登记表跑在 tmp_path 下的 sqlite 文件库上(与运行期同一个
`create_session_factory` / `session_scope`),不依赖任何外部服务。
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workspace.core.config import Settings
from workspace.models.shell import ShellSessionRecord
from workspace.services.shell_sessions import (
    ShellSessionRegistry,
    build_shell_session_registry,
    resolve_instance_id,
)

ACCOUNT_ID = "t-1"


async def _registry(
    tmp_path: Path, *, instance_id: str = "inst-a", lease_seconds: float = 120.0
) -> tuple[ShellSessionRegistry, async_sessionmaker[AsyncSession]]:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/shell.db")
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return (
        ShellSessionRegistry(
            factory, instance_id=instance_id, lease_seconds=lease_seconds
        ),
        factory,
    )


async def _register(
    registry: ShellSessionRegistry,
    *,
    session_id: str = "s-1",
    sandbox_id: str = "sb-1",
    provider: str = "fake",
    pid: int | None = 42,
) -> None:
    await registry.register(
        account_id=ACCOUNT_ID,
        sandbox_id=sandbox_id,
        session_id=session_id,
        provider=provider,
        runtime_name="bm-1",
        provider_pid=pid,
        cwd="/workspace",
        cols=120,
        rows=32,
        tty=True,
        created_at=datetime.now(UTC),
    )


async def _patch_row(
    factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str = "s-1",
    owner: str | None = None,
    expired: bool = False,
    status: str | None = None,
    closed_at: datetime | None = None,
) -> None:
    """直接改一行(模拟"时间过去"或"别的实例写的行")。"""
    async with session_scope(factory) as session:
        record = await session.scalar(
            select(ShellSessionRecord).where(
                ShellSessionRecord.session_id == session_id
            )
        )
        assert record is not None
        if owner is not None:
            record.owner_instance = owner
        if expired:
            record.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        if status is not None:
            record.status = status
        if closed_at is not None:
            record.closed_at = closed_at


# --- 登记 / 读取 ------------------------------------------------------------


async def test_register_then_lookup_roundtrip(tmp_path: Path) -> None:
    registry, _ = await _registry(tmp_path)
    await _register(registry)

    claim = await registry.lookup(ACCOUNT_ID, "sb-1", "s-1")

    assert claim is not None
    assert claim.owner_instance == "inst-a"
    assert claim.status == "running"
    assert claim.provider == "fake"
    assert claim.provider_pid == 42
    assert claim.tty is True
    assert claim.created_at.tzinfo is not None
    assert claim.lease_expired() is False


async def test_lookup_unknown_session_is_none(tmp_path: Path) -> None:
    registry, _ = await _registry(tmp_path)

    assert await registry.lookup(ACCOUNT_ID, "sb-1", "nope") is None


async def test_lookup_is_scoped_by_sandbox(tmp_path: Path) -> None:
    registry, _ = await _registry(tmp_path)
    await _register(registry)

    assert await registry.lookup(ACCOUNT_ID, "sb-other", "s-1") is None


async def test_count_active_ignores_expired_lease(tmp_path: Path) -> None:
    registry, factory = await _registry(tmp_path)
    await _register(registry, session_id="s-1")
    await _register(registry, session_id="s-2")
    await _patch_row(factory, session_id="s-2", expired=True)

    assert await registry.count_active(ACCOUNT_ID, "sb-1") == 1


async def test_close_sandbox_marks_all_running_rows_closed(tmp_path: Path) -> None:
    registry, factory = await _registry(tmp_path)
    await _register(registry, session_id="s-1")
    await _register(registry, session_id="s-2")

    closed = await registry.close_sandbox(ACCOUNT_ID, "sb-1")

    assert closed == 2
    async with session_scope(factory) as session:
        rows = list(await session.scalars(select(ShellSessionRecord)))
    assert {row.status for row in rows} == {"closed"}
    assert all(row.closed_at is not None for row in rows)


# --- 续租与接管 ------------------------------------------------------------


async def test_touch_only_renews_rows_owned_by_this_instance(tmp_path: Path) -> None:
    registry_a, factory = await _registry(tmp_path, instance_id="inst-a")
    registry_b, _ = await _registry(tmp_path, instance_id="inst-b")
    await _register(registry_a, session_id="s-1")
    await _patch_row(factory, session_id="s-1", expired=True)

    # 别人的行续不上(否则会替死掉的实例续命)
    assert await registry_b.touch([(ACCOUNT_ID, "sb-1", "s-1")]) == 0

    assert await registry_a.touch([(ACCOUNT_ID, "sb-1", "s-1")]) == 1
    claim = await registry_a.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert claim is not None and claim.lease_expired() is False


async def test_adopt_takes_over_expired_lease(tmp_path: Path) -> None:
    registry_a, factory = await _registry(tmp_path, instance_id="inst-a")
    registry_b, _ = await _registry(tmp_path, instance_id="inst-b")
    await _register(registry_a)
    await _patch_row(factory, expired=True)
    claim = await registry_a.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert claim is not None

    assert await registry_b.adopt(claim) is True

    taken = await registry_b.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert taken is not None
    assert taken.owner_instance == "inst-b"
    assert taken.lease_expired() is False


async def test_adopt_rejects_live_lease_of_another_instance(tmp_path: Path) -> None:
    registry_a, _ = await _registry(tmp_path, instance_id="inst-a")
    registry_b, _ = await _registry(tmp_path, instance_id="inst-b")
    await _register(registry_a)
    claim = await registry_a.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert claim is not None

    assert await registry_b.adopt(claim) is False

    still = await registry_b.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert still is not None and still.owner_instance == "inst-a"


async def test_adopt_is_a_single_winner(tmp_path: Path) -> None:
    registry_a, factory = await _registry(tmp_path, instance_id="inst-a")
    registry_b, _ = await _registry(tmp_path, instance_id="inst-b")
    registry_c, _ = await _registry(tmp_path, instance_id="inst-c")
    await _register(registry_a)
    await _patch_row(factory, expired=True)
    claim = await registry_a.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert claim is not None

    # b 先接管成功 → 租约立刻有效,c 再来只能失败(不会两边都以为自己持有)
    assert await registry_b.adopt(claim) is True
    assert await registry_c.adopt(claim) is False


async def test_adopt_refuses_closed_row(tmp_path: Path) -> None:
    registry_a, factory = await _registry(tmp_path, instance_id="inst-a")
    registry_b, _ = await _registry(tmp_path, instance_id="inst-b")
    await _register(registry_a)
    await _patch_row(factory, expired=True, status="closed")
    claim = await registry_a.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert claim is not None

    assert await registry_b.adopt(claim) is False


# --- 失联与清理 ------------------------------------------------------------


async def test_mark_closed_then_purge_by_retention(tmp_path: Path) -> None:
    registry, factory = await _registry(tmp_path)
    await _register(registry)

    assert await registry.mark_closed([(ACCOUNT_ID, "sb-1", "s-1")], exit_code=0) == 1
    claim = await registry.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert claim is not None and claim.status == "closed"

    assert await registry.purge_closed(retention_seconds=0) == 1
    assert await registry.lookup(ACCOUNT_ID, "sb-1", "s-1") is None


async def test_purge_keeps_running_rows(tmp_path: Path) -> None:
    registry, _ = await _registry(tmp_path)
    await _register(registry)

    assert await registry.purge_closed(retention_seconds=0) == 0
    assert await registry.lookup(ACCOUNT_ID, "sb-1", "s-1") is not None


async def test_list_stale_returns_only_expired_running_rows(tmp_path: Path) -> None:
    registry, factory = await _registry(tmp_path)
    await _register(registry, session_id="s-live")
    await _register(registry, session_id="s-stale")
    await _register(registry, session_id="s-closed")
    await _patch_row(factory, session_id="s-stale", expired=True)
    await _patch_row(factory, session_id="s-closed", expired=True, status="closed")

    stale = await registry.list_stale(limit=10)

    assert [claim.session_id for claim in stale] == ["s-stale"]


async def test_mark_lost_is_idempotent(tmp_path: Path) -> None:
    registry, _ = await _registry(tmp_path)
    await _register(registry)
    claim = await registry.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert claim is not None

    await registry.mark_lost(claim, reason="owner lease expired")
    await registry.mark_lost(claim, reason="owner lease expired")

    after = await registry.lookup(ACCOUNT_ID, "sb-1", "s-1")
    assert after is not None and after.status == "lost"


# --- 实例身份 --------------------------------------------------------------


def test_resolve_instance_id_prefers_configured_value() -> None:
    settings = Settings(_env_file=None, instance_id="  inst-explicit  ")

    assert resolve_instance_id(settings) == "inst-explicit"


def test_resolve_instance_id_falls_back_to_host_and_pid() -> None:
    settings = Settings(_env_file=None, instance_id="")

    resolved = resolve_instance_id(settings)

    assert resolved == f"{socket.gethostname()}:{os.getpid()}"


def test_build_registry_uses_settings(tmp_path: Path) -> None:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/x.db")
    settings = Settings(
        _env_file=None, instance_id="inst-x", sandbox_shell_lease_seconds=7
    )

    registry = build_shell_session_registry(factory, settings)

    assert registry.instance_id == "inst-x"
