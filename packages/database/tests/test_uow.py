"""工作单元:提交/回滚、仓储缓存、跨仓储同一事务。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.base import Base
from database.session import create_session_factory, session_scope
from database.uow import UnitOfWork
from tests.test_repository import (
    GlobalThing,
    ThingRepository,
    Widget,
    WidgetRepository,
)


@pytest.fixture()
async def factory(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/uow.db")
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await factory.engine.dispose()


async def test_commit_on_success(factory) -> None:
    async with UnitOfWork(factory, repositories=(WidgetRepository,)) as uow:
        await uow.repo(WidgetRepository).add(Widget(tenant_id="t-1", name="甲"))

    async with session_scope(factory) as session:
        assert await WidgetRepository(session).count_for_tenant("t-1") == 1


async def test_rollback_on_error_and_exception_propagates(factory) -> None:
    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        async with UnitOfWork(factory) as uow:
            await uow.repo(WidgetRepository).add(Widget(tenant_id="t-1", name="甲"))
            raise Boom("中途失败")

    async with session_scope(factory) as session:
        assert await WidgetRepository(session).count_for_tenant("t-1") == 0


async def test_cross_repository_share_one_transaction(factory) -> None:
    """跨仓储同一事务:第二个失败时第一个也回滚(原子性)。"""
    async with session_scope(factory) as session:
        existing = await ThingRepository(session).add(GlobalThing(name="重名"))

    class Fail(RuntimeError):
        pass

    with pytest.raises(Fail):
        async with UnitOfWork(factory) as uow:
            await uow.repo(WidgetRepository).add(Widget(tenant_id="t-1", name="甲"))
            thing = await uow.repo(ThingRepository).add(GlobalThing(name="乙"))
            assert thing.id
            raise Fail(f"后续步骤失败(已有 {existing.name})")

    async with session_scope(factory) as session:
        # 两个仓储的写入一起回滚
        assert await WidgetRepository(session).count_for_tenant("t-1") == 0
        assert await ThingRepository(session).count() == 1


async def test_repository_instances_are_cached_per_uow(factory) -> None:
    async with UnitOfWork(factory) as uow:
        assert uow.repo(WidgetRepository) is uow.repo(WidgetRepository)
        assert uow.repo(WidgetRepository).session is uow.session
        assert uow.repo(WidgetRepository) is not uow.repo(ThingRepository)


async def test_repo_outside_context_is_rejected(factory) -> None:
    uow = UnitOfWork(factory)
    with pytest.raises(RuntimeError, match="async with"):
        uow.repo(WidgetRepository)

    async with uow as entered:
        assert entered.session is not None
    assert uow.session is None  # 退出后不持有 session
