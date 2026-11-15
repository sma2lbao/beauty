"""长期记忆存储层单测:软删除/过期与 MemoryProvider 扩展点(临时 sqlite 库,零网络)。

本地嵌入/写入/列表检索见 test_memory_store.py 与 test_memory_store_query.py。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from common.errors import NotFoundError
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.repositories import (
    MemoryRepository,
)
from agent_orchestrator.services import memory as memory_service
from tests.conftest import _settings

#: 记忆服务只认 account_id 字符串;这里与 conftest 的 bearer 账号保持一致。
ACCOUNT = "t-1"
OTHER = "t-2"


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose,避免句柄残留。"""
    settings = _settings(tmp_path, "memory-store-lifecycle.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


# ---- 软删除/过期 ----


async def test_soft_deleted_memory_hidden_and_counts_updated(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        memory = await memory_service.write_memory(session, ACCOUNT, content="待删除")
        memory_id = memory.id
    async with session_scope(factory) as session:
        await memory_service.delete_memory(session, ACCOUNT, memory_id)
        assert await memory_service.list_memories(session, ACCOUNT) == []
        assert await memory_service.search_memories(session, ACCOUNT, "待删除") == []
        assert await memory_service.memory_stats(session, ACCOUNT) == {
            "total": 0,
            "active": 0,
            "pinned": 0,
            "expired": 0,
            "superseded": 0,
            "pending_reviews": 0,
        }


async def test_delete_memory_cross_account_is_404(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        memory = await memory_service.write_memory(
            session, ACCOUNT, content="账号一的记忆"
        )
        memory_id = memory.id
    with pytest.raises(NotFoundError) as excinfo:
        async with session_scope(factory) as session:
            await memory_service.delete_memory(session, OTHER, memory_id)
    assert excinfo.value.code == "memory.not_found"
    assert excinfo.value.details == {"memory_id": memory_id}
    # 跨账号删除失败后,原记忆仍在
    async with session_scope(factory) as session:
        assert (
            await MemoryRepository(session).get_active(ACCOUNT, memory_id) is not None
        )
        with pytest.raises(NotFoundError):
            await memory_service.delete_memory(session, ACCOUNT, "missing-id")


async def test_expired_memory_hidden_counted_and_purged(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        memory = await memory_service.write_memory(
            session, ACCOUNT, content="会过期的记忆", ttl_days=1
        )
        memory_id = memory.id
    # 直接改库把过期时间拨到过去(避免 sleep,语义由仓储的 now 比较覆盖)
    async with session_scope(factory) as session:
        await session.execute(
            update(Memory)
            .where(Memory.id == memory_id)
            .values(expires_at=datetime.now(UTC) - timedelta(days=1))
        )
    async with session_scope(factory) as session:
        repository = MemoryRepository(session)
        assert await repository.get_active(ACCOUNT, memory_id) is None
        assert memory_id not in [
            row.id for row in await repository.list_active(ACCOUNT)
        ]
        assert await repository.counts(ACCOUNT) == {
            "total": 1,
            "active": 0,
            "pinned": 0,
            "expired": 1,
            "superseded": 0,
        }
        assert await memory_service.purge_expired(session, ACCOUNT) == 1
        # 幂等:已标记删除的不会再被清理一次
        assert await memory_service.purge_expired(session, ACCOUNT) == 0
        assert await memory_service.memory_stats(session, ACCOUNT) == {
            "total": 0,
            "active": 0,
            "pinned": 0,
            "expired": 0,
            "superseded": 0,
            "pending_reviews": 0,
        }


# ---- MemoryProvider 扩展点 ----


async def test_local_memory_provider_roundtrip(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = memory_service.build_memory_provider()
    assert isinstance(provider, memory_service.LocalMemoryProvider)
    async with session_scope(factory) as session:
        # remember 返回记忆 id(远程 provider 只回 id,契约统一为 str)。
        memory_id = await provider.remember(
            session, ACCOUNT, "用户偏好简洁回答", kind="preference"
        )
        assert "用户偏好简洁回答" in await provider.recall(session, ACCOUNT, "回答风格")
    async with session_scope(factory) as session:
        await provider.forget(session, ACCOUNT, memory_id)
    async with session_scope(factory) as session:
        assert await provider.recall(session, ACCOUNT, "回答风格") == ""
