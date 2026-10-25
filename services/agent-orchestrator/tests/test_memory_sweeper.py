"""记忆 sweeper 单测:过期清理、近重复巩固、租户故障隔离与循环开关。

用临时 sqlite 文件库 + 假容器(SimpleNamespace 即可):单轮直接调 sweep_once,
循环用 monkeypatch 替换 sweep_once 验证参数/重试/取消,零网络、不依赖真实时钟。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services import memory_sweeper
from tests.conftest import _settings

#: 与 conftest 的 bearer 租户保持一致。
TENANT = "t-1"
OTHER = "t-2"


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose,避免句柄残留。"""
    settings = _settings(tmp_path, "memory-sweeper.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


def _container(
    session_factory: Any,
    *,
    interval: float = 0.01,
    consolidation_enabled: bool = True,
    threshold: float = 0.92,
) -> SimpleNamespace:
    """sweeper_loop 只读 settings 与 session_factory,假容器足够。"""
    settings = SimpleNamespace(
        memory_sweep_interval_seconds=interval,
        memory_consolidation_enabled=consolidation_enabled,
        memory_consolidation_threshold=threshold,
    )
    return SimpleNamespace(settings=settings, session_factory=session_factory)


async def _stop(task: asyncio.Task[None]) -> None:
    """取消后台任务并等它退出,避免用例之间留下悬挂 task。"""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ---- 单轮 sweep ----


async def test_sweep_once_purges_expired_and_consolidates(factory: Any) -> None:
    async with session_scope(factory) as session:
        expired = await memory_service.write_memory(
            session, TENANT, content="会过期的记忆", ttl_days=1
        )
        expired.expires_at = datetime.now(UTC) - timedelta(days=1)
        await memory_service.write_memory(session, TENANT, content="用户偏好简洁回答")
        await memory_service.write_memory(
            session, TENANT, content="用户偏好简洁回答,不要长篇大论"
        )
        await memory_service.write_memory(session, OTHER, content="另一个租户的记忆")

    totals = await memory_sweeper.sweep_once(factory)
    assert totals == {"tenants": 2, "purged": 1, "consolidated": 1}

    async with session_scope(factory) as session:
        tenant_stats = await memory_service.memory_stats(session, TENANT)
        other_stats = await memory_service.memory_stats(session, OTHER)
    # 过期的一条被清掉,近重复的两条合并成一条,共剩一条生效记忆
    assert tenant_stats["active"] == 1
    assert other_stats["active"] == 1


async def test_sweep_once_can_skip_consolidation(factory: Any) -> None:
    async with session_scope(factory) as session:
        expired = await memory_service.write_memory(
            session, TENANT, content="会过期的记忆", ttl_days=1
        )
        expired.expires_at = datetime.now(UTC) - timedelta(days=1)
        await memory_service.write_memory(session, TENANT, content="用户偏好简洁回答")
        await memory_service.write_memory(
            session, TENANT, content="用户偏好简洁回答,不要长篇大论"
        )

    totals = await memory_sweeper.sweep_once(factory, consolidation_enabled=False)
    assert totals == {"tenants": 1, "purged": 1, "consolidated": 0}

    async with session_scope(factory) as session:
        stats = await memory_service.memory_stats(session, TENANT)
    assert stats["active"] == 2


async def test_sweep_once_isolates_tenant_failures(
    factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(session, TENANT, content="用户偏好简洁回答")
        await memory_service.write_memory(
            session, TENANT, content="用户偏好简洁回答,不要长篇大论"
        )
        await memory_service.write_memory(session, OTHER, content="另一个租户的记忆")

    async def failing_consolidate(
        session: AsyncSession,
        tenant_id: str,
        *,
        threshold: float = 0.92,
    ) -> int:
        if tenant_id == TENANT:
            raise RuntimeError("巩固失败")
        return 0

    monkeypatch.setattr(memory_sweeper, "consolidate", failing_consolidate)
    totals = await memory_sweeper.sweep_once(factory)
    assert totals["tenants"] == 2
    assert totals["consolidated"] == 0

    async with session_scope(factory) as session:
        tenant_stats = await memory_service.memory_stats(session, TENANT)
        other_stats = await memory_service.memory_stats(session, OTHER)
    # 失败租户的事务已回滚(两条都还在),其他租户继续被处理
    assert tenant_stats["active"] == 2
    assert other_stats["active"] == 1


# ---- 循环启停 ----


async def test_sweeper_loop_returns_when_interval_disabled(factory: Any) -> None:
    container = _container(factory, interval=0.0)
    assert (
        await asyncio.wait_for(memory_sweeper.sweeper_loop(container), timeout=1)
        is None
    )


async def test_sweeper_loop_returns_without_session_factory() -> None:
    container = _container(None, interval=60.0)
    assert (
        await asyncio.wait_for(memory_sweeper.sweeper_loop(container), timeout=1)
        is None
    )


async def test_sweeper_loop_survives_iteration_failure(
    factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0
    recovered = asyncio.Event()

    async def flaky_sweep_once(*args: Any, **kwargs: Any) -> dict[str, int]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("本轮失败")
        recovered.set()
        return {"tenants": 0, "purged": 0, "consolidated": 0}

    monkeypatch.setattr(memory_sweeper, "sweep_once", flaky_sweep_once)
    task = memory_sweeper.start_sweeper(_container(factory))
    try:
        await asyncio.wait_for(recovered.wait(), timeout=2)
    finally:
        await _stop(task)
    # 单轮异常只记日志,下一轮继续
    assert attempts >= 2


async def test_start_sweeper_uses_settings_and_is_cancellable(
    factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    started = asyncio.Event()

    async def fake_sweep_once(
        session_factory: Any,
        *,
        consolidation_enabled: bool = True,
        threshold: float = 0.92,
    ) -> dict[str, int]:
        calls.append(
            {
                "session_factory": session_factory,
                "consolidation_enabled": consolidation_enabled,
                "threshold": threshold,
            }
        )
        started.set()
        return {"tenants": 0, "purged": 0, "consolidated": 0}

    monkeypatch.setattr(memory_sweeper, "sweep_once", fake_sweep_once)
    container = _container(factory, consolidation_enabled=True, threshold=0.8)
    task = memory_sweeper.start_sweeper(container)
    assert task.get_name() == "orchestrator-memory-sweeper"
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        await _stop(task)

    assert task.cancelled()
    assert len(calls) == 1
    assert calls[0]["session_factory"] is factory
    assert calls[0]["consolidation_enabled"] is True
    assert calls[0]["threshold"] == 0.8
