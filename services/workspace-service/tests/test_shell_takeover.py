"""跨实例接管:登记行、归属判定、接管与孤儿回收。

"上一个实例进程消失"不真跑一遍旧管理器——读循环被取消时会在 finally 里
把登记行标成 closed,模拟不出崩溃现场。这里直接用 provider 造一个仍留在
沙箱里的 pty,再按旧实例身份写登记行(pid/owner/过期租约),让新实例的
归属判定去接管或回收它。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from common.errors import ConflictError
from database.base import Base
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import ACCOUNT, FakeSandboxProvider
from workspace.models.shell import ShellSessionRecord
from workspace.services.shell_sessions import ShellSessionManager, ShellSessionRegistry

ACCOUNT_ID = "t-1"
SANDBOX_ID = "sb-1"
OLD_INSTANCE = "inst-old"


async def _setup(
    tmp_path: Path, *, instance_id: str = "inst-new"
) -> tuple[ShellSessionRegistry, async_sessionmaker[AsyncSession]]:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/shell.db")
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return ShellSessionRegistry(factory, instance_id=instance_id), factory


async def _orphan(
    provider: FakeSandboxProvider,
    registry: ShellSessionRegistry,
    factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str = "s-1",
    owner: str = OLD_INSTANCE,
    expired: bool = True,
    provider_name: str = "fake",
    pid: int | None = None,
) -> Any:
    """造一个"旧实例留下的会话":pty 还在,登记行归旧实例且租约按需过期。"""
    shell = await provider.open_shell("bm-1", cwd="/workspace")
    await registry.register(
        account_id=ACCOUNT_ID,
        sandbox_id=SANDBOX_ID,
        session_id=session_id,
        provider=provider_name,
        runtime_name="bm-1",
        provider_pid=shell.pid if pid is None else pid,
        cwd="/workspace",
        cols=120,
        rows=32,
        tty=True,
        created_at=datetime.now(UTC),
    )
    async with session_scope(factory) as session:
        row = await session.scalar(
            select(ShellSessionRecord).where(
                ShellSessionRecord.session_id == session_id
            )
        )
        assert row is not None
        row.owner_instance = owner
        if expired:
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    return shell


async def _row(
    factory: async_sessionmaker[AsyncSession], session_id: str = "s-1"
) -> ShellSessionRecord:
    async with session_scope(factory) as session:
        row = await session.scalar(
            select(ShellSessionRecord).where(
                ShellSessionRecord.session_id == session_id
            )
        )
        assert row is not None
        return row


async def _claim(manager: ShellSessionManager, session_id: str = "s-1") -> None:
    await manager.ensure(
        account_id=ACCOUNT_ID, sandbox_id=SANDBOX_ID, session_id=session_id
    )


# --- 登记:开会话就写行 -----------------------------------------------------


async def test_open_registers_owner_and_provider_pid(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, _ = await _setup(tmp_path)
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    info = await manager.open(
        account_id=ACCOUNT_ID,
        sandbox_id=SANDBOX_ID,
        runtime_name="bm-1",
        provider=sandbox_provider,
        cwd="/workspace",
    )

    claim = await registry.lookup(ACCOUNT_ID, SANDBOX_ID, info.session_id)
    assert claim is not None
    assert claim.owner_instance == "inst-new"
    assert claim.provider == "fake"
    assert claim.provider_pid == sandbox_provider.shells[-1].pid
    assert claim.status == "running"
    await manager.shutdown()


# --- 接管:租约过期后新实例接着往下走 --------------------------------------


async def test_takeover_resumes_output_stream(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, factory = await _setup(tmp_path)
    shell = await _orphan(sandbox_provider, registry, factory)
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    await _claim(manager)

    # 接管的是同一个 pty(按 pid 重连),不是新开一个
    assert sandbox_provider.attach_calls == [
        {"name": "bm-1", "pid": shell.pid, "cols": 120, "rows": 32}
    ]
    assert len(sandbox_provider.shells) == 1
    backlog, queue = manager.subscribe(
        account_id=ACCOUNT_ID, sandbox_id=SANDBOX_ID, session_id="s-1"
    )
    assert backlog == []

    shell.emit(b"after takeover\r\n")

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event is not None and event.kind == "output"
    assert event.data == "after takeover\r\n"
    await manager.shutdown()


async def test_takeover_renews_lease_so_others_cannot_take_it(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, factory = await _setup(tmp_path)
    await _orphan(sandbox_provider, registry, factory)
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    await _claim(manager)

    row = await _row(factory)
    assert row.owner_instance == "inst-new"
    assert row.lease_expires_at.replace(tzinfo=UTC) > datetime.now(UTC)
    await manager.shutdown()


# --- 明确契约:别人的会话给 409,不抢也不杀 --------------------------------


async def test_elsewhere_when_owner_lease_is_live(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, factory = await _setup(tmp_path)
    shell = await _orphan(sandbox_provider, registry, factory, expired=False)
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    with pytest.raises(ConflictError) as excinfo:
        await _claim(manager)

    assert excinfo.value.code == "sandbox.session_elsewhere"
    assert excinfo.value.details["owner_instance"] == OLD_INSTANCE
    assert excinfo.value.details["resumable"] is True
    # 关键:判归属失败时绝不碰 pty(否则会把别人的会话杀掉)
    assert sandbox_provider.attach_calls == []
    assert shell.closed is False


async def test_closed_session_reports_session_closed(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, factory = await _setup(tmp_path)
    await _orphan(sandbox_provider, registry, factory)
    async with session_scope(factory) as session:
        row = await session.scalar(select(ShellSessionRecord))
        assert row is not None
        row.status = "closed"
        row.closed_at = datetime.now(UTC)
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    with pytest.raises(ConflictError) as excinfo:
        await _claim(manager)

    assert excinfo.value.code == "sandbox.session_closed"


async def test_lost_session_reports_session_lost(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, factory = await _setup(tmp_path)
    await _orphan(sandbox_provider, registry, factory)
    async with session_scope(factory) as session:
        row = await session.scalar(select(ShellSessionRecord))
        assert row is not None
        row.status = "lost"
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    with pytest.raises(ConflictError) as excinfo:
        await _claim(manager)

    assert excinfo.value.code == "sandbox.session_lost"
    assert sandbox_provider.attach_calls == []


async def test_provider_mismatch_marks_lost(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    # 登记行说这个 pty 在 e2b 上,本实例却只有 fake provider:接管不了,标 lost
    registry, factory = await _setup(tmp_path)
    await _orphan(sandbox_provider, registry, factory, provider_name="e2b")
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    with pytest.raises(ConflictError) as excinfo:
        await _claim(manager)

    assert excinfo.value.code == "sandbox.session_lost"
    assert (await _row(factory)).status == "lost"
    assert sandbox_provider.attach_calls == []


async def test_attach_failure_marks_lost(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, factory = await _setup(tmp_path)
    await _orphan(sandbox_provider, registry, factory)
    sandbox_provider.fail_attach = True
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    with pytest.raises(ConflictError) as excinfo:
        await _claim(manager)

    assert excinfo.value.code == "sandbox.session_lost"
    assert len(sandbox_provider.attach_calls) == 1
    assert (await _row(factory)).status == "lost"
    with pytest.raises(ConflictError) as again:
        await _claim(manager)
    assert again.value.code == "sandbox.session_lost"


# --- 孤儿回收:持有者不会回来了 -------------------------------------------


async def test_sweep_reclaims_orphan_and_closes_its_pty(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, factory = await _setup(tmp_path)
    shell = await _orphan(sandbox_provider, registry, factory)
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    reaped = await manager.sweep()

    assert reaped == 1
    assert [call["pid"] for call in sandbox_provider.attach_calls] == [shell.pid]
    assert shell.closed is True
    assert (await _row(factory)).status == "lost"


async def test_sweep_keeps_session_owned_by_another_instance(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, factory = await _setup(tmp_path)
    shell = await _orphan(sandbox_provider, registry, factory, expired=False)
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)

    assert await manager.sweep() == 0

    # 租约还有效 → 不是孤儿,既不接管也不关 pty
    assert sandbox_provider.attach_calls == []
    assert shell.closed is False
    assert (await _row(factory)).status == "running"


# --- 关闭落库 --------------------------------------------------------------


async def test_close_and_shutdown_persist_closed_status(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    registry, factory = await _setup(tmp_path)
    manager = ShellSessionManager(registry=registry, provider=sandbox_provider)
    info = await manager.open(
        account_id=ACCOUNT_ID,
        sandbox_id=SANDBOX_ID,
        runtime_name="bm-1",
        provider=sandbox_provider,
        cwd="/workspace",
    )

    assert await manager.close(
        account_id=ACCOUNT_ID, sandbox_id=SANDBOX_ID, session_id=info.session_id
    )
    await manager.shutdown()

    row = await _row(factory, info.session_id)
    assert row.status == "closed"
    assert row.closed_at is not None
    assert row.exit_code == -1


# --- HTTP:跨实例时是 409,不是"回放空然后静默结束" ------------------------

REMOTE_INSTANCE = "inst-remote"


def _create_sandbox(client: TestClient) -> dict[str, Any]:
    response = client.post("/v1/sandboxes", json={}, headers=ACCOUNT)
    assert response.status_code == 201, response.text
    return response.json()


async def _insert_remote_session(
    client: TestClient, sandbox_id: str, *, session_id: str = "s-remote"
) -> None:
    registry = ShellSessionRegistry(
        client.app.state.session_factory, instance_id=REMOTE_INSTANCE
    )
    await registry.register(
        account_id=ACCOUNT_ID,
        sandbox_id=sandbox_id,
        session_id=session_id,
        provider="fake",
        runtime_name="bm-1",
        provider_pid=7,
        cwd="/workspace",
        cols=120,
        rows=32,
        tty=True,
        created_at=datetime.now(UTC),
    )


async def test_stream_of_remote_session_is_409_elsewhere(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    await _insert_remote_session(client, sandbox["id"])

    with client.stream(
        "GET",
        f"/v1/sandboxes/{sandbox['id']}/sessions/s-remote/stream",
        headers=ACCOUNT,
    ) as response:
        body = b"".join(response.iter_raw())
        status = response.status_code

    assert status == 409
    assert b"sandbox.session_elsewhere" in body
    # 只是判归属,不该产生任何 pty 连接
    assert sandbox_provider.attach_calls == []


async def test_input_of_remote_session_is_409_elsewhere(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create_sandbox(client)
    await _insert_remote_session(client, sandbox["id"])

    response = client.post(
        f"/v1/sandboxes/{sandbox['id']}/sessions/s-remote/input",
        json={"data": "ls\n"},
        headers=ACCOUNT,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "sandbox.session_elsewhere"
    assert response.json()["details"]["owner_instance"] == REMOTE_INSTANCE
