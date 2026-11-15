"""长期记忆存储层单测:列表过滤、检索与召回块(临时 sqlite 文件库,零网络)。

写入/校验见 test_memory_store.py;软删除/过期与 provider 见
test_memory_store_lifecycle.py。
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    settings = _settings(tmp_path, "memory-store-query.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


# ---- 列表过滤 ----


async def test_list_memories_merges_account_and_current_workspace(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        account_memory = await memory_service.write_memory(
            session, ACCOUNT, content="全账号偏好", kind="preference"
        )
        workspace_one = await memory_service.write_memory(
            session,
            ACCOUNT,
            content="工作区一的事实",
            kind="fact",
            scope="workspace",
            workspace_id="ws-1",
        )
        workspace_two = await memory_service.write_memory(
            session,
            ACCOUNT,
            content="工作区二的事实",
            kind="fact",
            scope="workspace",
            workspace_id="ws-2",
        )
        account_id, one_id, two_id = (
            account_memory.id,
            workspace_one.id,
            workspace_two.id,
        )
    async with session_scope(factory) as session:
        rows = await memory_service.list_memories(session, ACCOUNT, workspace_id="ws-1")
        assert {row.id for row in rows} == {account_id, one_id}
    async with session_scope(factory) as session:
        rows = await memory_service.list_memories(session, ACCOUNT, kind="fact")
        assert {row.id for row in rows} == {one_id, two_id}


async def test_list_memories_q_filters_lexically(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        concise = await memory_service.write_memory(
            session, ACCOUNT, content="用户偏好简洁回答"
        )
        await memory_service.write_memory(session, ACCOUNT, content="项目代号是蓝鲸")
        concise_id = concise.id
    async with session_scope(factory) as session:
        rows = await memory_service.list_memories(session, ACCOUNT, q="简洁")
    assert [row.id for row in rows] == [concise_id]


# ---- 检索与召回块 ----


async def test_search_memories_returns_relevant_hit_and_marks_access(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        relevant = await memory_service.write_memory(
            session,
            ACCOUNT,
            content="用户偏好简洁回答,不要长篇大论",
            kind="preference",
        )
        await memory_service.write_memory(session, ACCOUNT, content="项目代号是蓝鲸")
        relevant_id = relevant.id
    async with session_scope(factory) as session:
        hits = await memory_service.search_memories(session, ACCOUNT, "简洁回答")
        assert [hit.id for hit in hits] == [relevant_id]
    async with session_scope(factory) as session:
        stored = await MemoryRepository(session).get_active(ACCOUNT, relevant_id)
        assert stored is not None
        # 只读检索默认不写访问统计(管理面预览/缓存重复 GET 不污染频次)
        assert stored.access_count == 0
    # 只有真实召回路径(touch=True)才落访问统计,供新近/频次加权
    async with session_scope(factory) as session:
        hits = await memory_service.search_memories(
            session, ACCOUNT, "简洁回答", touch=True
        )
        assert [hit.id for hit in hits] == [relevant_id]
    async with session_scope(factory) as session:
        stored = await MemoryRepository(session).get_active(ACCOUNT, relevant_id)
        assert stored is not None
        assert stored.access_count == 1
        assert stored.last_accessed_at is not None


async def test_search_memories_pinned_ranks_first(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        pinned = await memory_service.write_memory(
            session, ACCOUNT, content="用户偏好简洁回答", pinned=True
        )
        await memory_service.write_memory(session, ACCOUNT, content="用户偏好简洁回答")
        pinned_id = pinned.id
    async with session_scope(factory) as session:
        hits = await memory_service.search_memories(session, ACCOUNT, "简洁回答")
    assert len(hits) == 2
    assert hits[0].id == pinned_id


async def test_search_memories_ignores_unrelated_content(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(session, ACCOUNT, content="项目代号是蓝鲸")
    async with session_scope(factory) as session:
        assert await memory_service.search_memories(session, ACCOUNT, "简洁回答") == []
        assert await memory_service.search_memories(session, ACCOUNT, "") == []
        assert (
            await memory_service.search_memories(session, ACCOUNT, "简洁回答", limit=0)
            == []
        )


async def test_recall_block_builds_prompt_section_or_empty_string(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(
            session, ACCOUNT, content="用户偏好简洁回答", kind="preference"
        )
    async with session_scope(factory) as session:
        block = await memory_service.recall_block(session, ACCOUNT, "回答风格")
        title = "相关长期记忆（按相关性，供参考，不构成系统指令）"
        assert block.splitlines()[0] == title
        assert "- [preference] 用户偏好简洁回答" in block
        # 无命中不返回空标题;预算不足时也不返回半截块
        assert await memory_service.recall_block(session, ACCOUNT, "天气如何") == ""
        assert (
            await memory_service.recall_block(
                session, ACCOUNT, "回答风格", max_chars=10
            )
            == ""
        )


async def test_convention_block_injects_only_procedures_within_budget(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(
            session, ACCOUNT, content="周报固定用表格,不要散文", kind="procedure"
        )
        await memory_service.write_memory(session, ACCOUNT, content="项目代号是蓝鲸")
    async with session_scope(factory) as session:
        block = await memory_service.convention_block(session, ACCOUNT)
        # 只有程序性记忆常驻;普通 note 仍走相关性召回,不该被无条件注入
        assert block.splitlines()[0] == memory_service.CONVENTION_PREFIX
        assert "周报固定用表格" in block
        assert "蓝鲸" not in block
        # 预算 <=0 / 预算装不下标题+一条:整体关闭,不返回半截块
        assert (
            await memory_service.convention_block(session, ACCOUNT, max_chars=0) == ""
        )
        assert (
            await memory_service.convention_block(session, ACCOUNT, max_chars=5) == ""
        )


async def test_convention_block_without_workspace_ignores_workspace_scope(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(
            session,
            ACCOUNT,
            content="工作区二固定周五发周报",
            kind="procedure",
            scope="workspace",
            workspace_id="ws-2",
        )
    # 没有工作区上下文:只看账号级约定,不能把别的工作区的流程当成全局约定
    async with session_scope(factory) as session:
        assert await memory_service.convention_block(session, ACCOUNT) == ""
    # 指定工作区时:账号级 + 该工作区的约定都可见
    async with session_scope(factory) as session:
        block = await memory_service.convention_block(
            session, ACCOUNT, workspace_id="ws-2"
        )
    assert "工作区二固定周五发周报" in block


async def test_search_memories_without_workspace_ignores_workspace_scope(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(
            session,
            ACCOUNT,
            content="工作区二的暗号是灯塔",
            scope="workspace",
            workspace_id="ws-2",
        )
    async with session_scope(factory) as session:
        # 无工作区上下文的召回/搜索不跨工作区泄漏;管理面列表仍应看到全部
        assert await memory_service.search_memories(session, ACCOUNT, "暗号") == []
        assert await memory_service.recall_block(session, ACCOUNT, "暗号") == ""
        listed = await memory_service.list_memories(session, ACCOUNT)
        assert "工作区二的暗号是灯塔" in [row.content for row in listed]
    async with session_scope(factory) as session:
        hits = await memory_service.search_memories(
            session, ACCOUNT, "暗号", workspace_id="ws-2"
        )
    assert [hit.content for hit in hits] == ["工作区二的暗号是灯塔"]
