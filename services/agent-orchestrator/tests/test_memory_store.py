"""长期记忆存储层单测:本地嵌入、仓储语义、服务校验与召回块。

纯函数(嵌入/切词)不碰库;库相关用例用临时 sqlite 文件库,建表走
Base.metadata.create_all(与既有测试一致)。服务函数全部显式传参,不依赖
Settings,所以这里不需要额外钉开关——数据库与记忆能力都不出网。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from common.errors import AppError, NotFoundError
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.repositories import (
    ConversationSummaryRepository,
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
    settings = _settings(tmp_path, "memory-store.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


# ---- 本地嵌入(纯函数) ----


def test_embed_text_is_deterministic_and_unit_norm() -> None:
    vector = memory_service.embed_text("用户偏好简洁回答")
    assert len(vector) == memory_service.EMBEDDING_DIMENSIONS
    assert vector == memory_service.embed_text("用户偏好简洁回答")
    assert vector != memory_service.embed_text("项目代号是蓝鲸")
    assert sum(value * value for value in vector) == pytest.approx(1.0)


def test_embed_text_whitespace_is_zero_vector() -> None:
    assert memory_service.embed_text("   ") == [0.0] * (
        memory_service.EMBEDDING_DIMENSIONS
    )
    assert memory_service.embed_text("") == [0.0] * (
        memory_service.EMBEDDING_DIMENSIONS
    )


# ---- 对话摘要仓储 ----


async def test_conversation_summary_upsert_keeps_one_row_per_conversation(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    covered = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    async with session_scope(factory) as session:
        repository = ConversationSummaryRepository(session)
        first = await repository.upsert(
            ACCOUNT,
            "c-1",
            summary="第一版摘要",
            covered_until=covered,
            covered_messages=3,
            tokens_before=120,
            tokens_after=20,
        )
        second = await repository.upsert(
            ACCOUNT,
            "c-1",
            summary="第二版摘要",
            covered_until=covered + timedelta(minutes=5),
            covered_messages=5,
            tokens_before=200,
            tokens_after=30,
        )
        assert second.id == first.id
    async with session_scope(factory) as session:
        repository = ConversationSummaryRepository(session)
        stored = await repository.get_for_conversation(ACCOUNT, "c-1")
        assert stored is not None
        assert stored.summary == "第二版摘要"
        assert stored.covered_messages == 5
        assert stored.tokens_before == 200
        # 账号隔离:同 conversation_id 在别的账号下不存在(不是 404,是 None)
        assert await repository.get_for_conversation(OTHER, "c-1") is None


# ---- 写入与校验 ----


async def test_write_memory_persists_local_embedding(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        memory = await memory_service.write_memory(
            session,
            ACCOUNT,
            content="用户偏好简洁回答",
            kind="preference",
            workspace_id="ws-1",
        )
    assert memory.scope == "account"
    # scope=account 时 workspace_id 被强制清空,避免出现"账号级却挂工作区"的脏数据
    assert memory.workspace_id is None
    assert memory.embedding_model == memory_service.EMBEDDING_MODEL
    assert memory.embedding is not None
    assert len(memory.embedding) == memory_service.EMBEDDING_DIMENSIONS
    assert memory.expires_at is None


async def test_write_memory_truncates_content_by_budget(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        memory = await memory_service.write_memory(
            session, ACCOUNT, content="长" * 20, max_content_chars=10
        )
    assert memory.content == "长" * 10


async def test_write_memory_ttl_only_sets_future_expiry(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        temporary = await memory_service.write_memory(
            session, ACCOUNT, content="临时记忆", ttl_days=7
        )
        permanent = await memory_service.write_memory(
            session, ACCOUNT, content="永久记忆", ttl_days=0
        )
    assert temporary.expires_at is not None
    assert permanent.expires_at is None


async def test_write_memory_rejects_invalid_input(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cases: list[tuple[dict[str, object], str]] = [
        ({"content": "   "}, "memory.empty"),
        ({"content": "x", "kind": "unknown"}, "memory.invalid_kind"),
        ({"content": "x", "scope": "team"}, "memory.invalid_scope"),
        ({"content": "x", "source_type": "api"}, "memory.invalid_source"),
        ({"content": "x", "scope": "workspace"}, "memory.workspace_required"),
    ]
    for kwargs, code in cases:
        with pytest.raises(AppError) as excinfo:
            async with session_scope(factory) as session:
                await memory_service.write_memory(session, ACCOUNT, **kwargs)
        assert excinfo.value.code == code


async def test_write_memory_enforces_entry_limit(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(
            session, ACCOUNT, content="第一条", max_entries=1
        )
        with pytest.raises(AppError) as excinfo:
            await memory_service.write_memory(
                session, ACCOUNT, content="第二条", max_entries=1
            )
    assert excinfo.value.code == "memory.limit_reached"


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
        # 命中要落访问统计,后续可以用新近/频次做加权
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
        assert block.splitlines()[0] == "相关长期记忆（按相关性，供参考）"
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
        }
        assert await memory_service.purge_expired(session, ACCOUNT) == 1
        # 幂等:已标记删除的不会再被清理一次
        assert await memory_service.purge_expired(session, ACCOUNT) == 0
        assert await memory_service.memory_stats(session, ACCOUNT) == {
            "total": 0,
            "active": 0,
            "pinned": 0,
            "expired": 0,
        }


# ---- MemoryProvider 扩展点 ----


async def test_local_memory_provider_roundtrip(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = memory_service.build_memory_provider()
    assert isinstance(provider, memory_service.LocalMemoryProvider)
    async with session_scope(factory) as session:
        memory = await provider.remember(
            session, ACCOUNT, "用户偏好简洁回答", kind="preference"
        )
        memory_id = memory.id
        assert "用户偏好简洁回答" in await provider.recall(session, ACCOUNT, "回答风格")
    async with session_scope(factory) as session:
        await provider.forget(session, ACCOUNT, memory_id)
    async with session_scope(factory) as session:
        assert await provider.recall(session, ACCOUNT, "回答风格") == ""
