"""async engine、session 工厂与事务边界。

用法(服务 lifespan,见 services/agent-orchestrator):

    factory = create_session_factory(settings.database_url)
    ...
    async with session_scope(factory) as session:
        ...

session_scope 负责提交/回滚/关闭;端点与业务层不手写事务。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def create_session_factory(
    url: str, **engine_kwargs: Any
) -> async_sessionmaker[AsyncSession]:
    """创建 session 工厂;``factory.engine`` 供 lifespan 做 dispose/create_all。"""
    engine = create_async_engine(url, poolclass=NullPool, **engine_kwargs)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    factory.engine = engine
    return factory


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """事务边界:成功提交,异常回滚,总是关闭。"""
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
