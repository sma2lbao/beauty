"""记忆生命周期(P3-B/R5–R8)单测:时效冲突、核心注入、局部更新、复核与合规删除。

与 test_memory_store.py 同口径:临时 sqlite 文件库 + Base.metadata.create_all,
服务函数显式传参,不依赖 Settings,零网络。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from common.errors import AppError, ConflictError, NotFoundError
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services import memory_reviews
from tests.conftest import _settings

ACCOUNT = "t-1"
OTHER = "t-2"


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = _settings(tmp_path, "memory-lifecycle.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


# ---- 敏感信息与相似度(纯函数) ----


def test_find_sensitive_flags_credentials() -> None:
    assert memory_service.find_sensitive("key: sk-abcdefghijklmnopqrstuvwxyz") == (
        "openai_key"
    )
    assert memory_service.find_sensitive("AKIAABCDEFGHIJKLMNOP") == "aws_key"
    assert (
        memory_service.find_sensitive("-----BEGIN RSA PRIVATE KEY-----")
        == "private_key"
    )
    assert memory_service.find_sensitive("卡号 4111 1111 1111 1111") == "credit_card"
    assert memory_service.find_sensitive("用户偏好周报用表格") is None


def test_content_similarity_is_containment_ratio() -> None:
    assert (
        memory_service.content_similarity("用户偏好简洁回答", "用户偏好简洁回答") == 1.0
    )
    assert memory_service.content_similarity("周报用表格", "周报用表格并且简洁") == 1.0
    assert memory_service.content_similarity("周报用表格", "月度总结") == 0.0


# ---- R5 时效与冲突 ----


async def test_similar_fact_supersedes_old_and_keeps_history(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        old = await memory_service.write_memory(
            session, ACCOUNT, content="用户偏好使用中文回复", kind="preference"
        )
    async with session_scope(factory) as session:
        new = await memory_service.write_memory(
            session,
            ACCOUNT,
            content="用户偏好使用英文回复",
            kind="preference",
            conflict_threshold=0.75,
        )
    async with session_scope(factory) as session:
        active = await memory_service.list_memories(session, ACCOUNT)
        history = await memory_service.list_memories(
            session, ACCOUNT, include_invalid=True
        )
        stats = await memory_service.memory_stats(session, ACCOUNT)
        stale = await memory_service.get_memory(session, ACCOUNT, old.id)
    assert [row.id for row in active] == [new.id]
    assert {row.id for row in history} == {old.id, new.id}
    assert (stale.invalid_at is not None, stale.superseded_by) == (True, new.id)
    assert stats["superseded"] == 1
    assert stats["active"] == 1


async def test_conflict_threshold_off_keeps_both_rows(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(
            session, ACCOUNT, content="用户偏好使用中文回复", kind="preference"
        )
        await memory_service.write_memory(
            session, ACCOUNT, content="用户偏好使用英文回复", kind="preference"
        )
    async with session_scope(factory) as session:
        active = await memory_service.list_memories(session, ACCOUNT)
    assert len(active) == 2


async def test_notes_never_supersede_even_if_similar(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(session, ACCOUNT, content="项目代号蓝鲸")
        await memory_service.write_memory(
            session, ACCOUNT, content="项目代号蓝鲸二号", conflict_threshold=0.5
        )
    async with session_scope(factory) as session:
        stats = await memory_service.memory_stats(session, ACCOUNT)
    assert stats["superseded"] == 0
    assert stats["active"] == 2


# ---- R6 核心记忆块 ----


async def test_core_block_renders_only_always_mode_non_procedures(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(
            session,
            ACCOUNT,
            content="称呼用户为老师",
            kind="preference",
            injection_mode="always",
        )
        await memory_service.write_memory(
            session, ACCOUNT, content="周报固定用表格", kind="procedure"
        )
        await memory_service.write_memory(session, ACCOUNT, content="项目代号蓝鲸")
    async with session_scope(factory) as session:
        block = await memory_service.core_block(session, ACCOUNT)
        empty = await memory_service.core_block(session, ACCOUNT, max_chars=0)
    assert block.splitlines()[0] == memory_service.CORE_PREFIX
    assert "称呼用户为老师" in block
    assert "周报固定用表格" not in block
    assert "项目代号蓝鲸" not in block
    assert empty == ""


async def test_procedure_cannot_be_always_injected(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        with pytest.raises(AppError) as caught:
            await memory_service.write_memory(
                session,
                ACCOUNT,
                content="周报固定用表格",
                kind="procedure",
                injection_mode="always",
            )
    assert caught.value.code == "memory.invalid_injection"


# ---- R4 编辑与乐观并发 ----


async def test_update_memory_recomputes_embedding_and_ttl(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        row = await memory_service.write_memory(
            session, ACCOUNT, content="旧内容", ttl_days=3
        )
        old_embedding = list(row.embedding or [])
    async with session_scope(factory) as session:
        updated = await memory_service.update_memory(
            session,
            ACCOUNT,
            row.id,
            content="新内容",
            kind="preference",
            pinned=True,
            ttl_days=0,
            ttl_days_set=True,
            injection_mode="always",
        )
    assert updated.content == "新内容"
    assert (updated.kind, updated.pinned, updated.injection_mode) == (
        "preference",
        True,
        "always",
    )
    assert updated.expires_at is None
    assert updated.embedding != old_embedding


async def test_update_memory_rejects_stale_token_and_sensitive_content(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        row = await memory_service.write_memory(session, ACCOUNT, content="待改内容")
    async with session_scope(factory) as session:
        with pytest.raises(ConflictError) as conflict:
            await memory_service.update_memory(
                session,
                ACCOUNT,
                row.id,
                content="新内容",
                expected_updated_at=datetime(2000, 1, 1, tzinfo=UTC),
            )
        with pytest.raises(AppError) as sensitive:
            await memory_service.update_memory(
                session, ACCOUNT, row.id, content="token: sk-abcdefghijklmnop"
            )
        with pytest.raises(NotFoundError):
            await memory_service.update_memory(session, OTHER, row.id, content="x")
    assert conflict.value.code == "memory.conflict"
    assert sensitive.value.code == "memory.sensitive_rejected"


async def test_update_memory_moves_scope_and_requires_workspace(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        row = await memory_service.write_memory(session, ACCOUNT, content="工作区约定")
        with pytest.raises(AppError) as caught:
            await memory_service.update_memory(
                session, ACCOUNT, row.id, scope="workspace"
            )
        moved = await memory_service.update_memory(
            session,
            ACCOUNT,
            row.id,
            scope="workspace",
            workspace_id="ws-1",
            workspace_id_set=True,
        )
    assert caught.value.code == "memory.workspace_required"
    assert (moved.scope, moved.workspace_id) == ("workspace", "ws-1")


# ---- R8 合规删除/导出 ----


async def test_export_and_purge_all_memories(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(session, ACCOUNT, content="账号一的记忆")
        await memory_service.write_memory(session, OTHER, content="账号二的记忆")
        await memory_reviews.queue_candidate(
            session, ACCOUNT, content="待复核的偏好", kind="preference"
        )
    async with session_scope(factory) as session:
        exported = await memory_service.export_memories(session, ACCOUNT)
        assert [row.content for row in exported] == ["账号一的记忆"]
        removed = await memory_service.purge_all_memories(session, ACCOUNT)
        assert removed == {"memories": 1, "reviews": 1}
        assert await memory_service.list_memories(session, ACCOUNT) == []
        assert await memory_reviews.list_reviews(session, ACCOUNT) == []
        other = await memory_service.list_memories(session, OTHER)
    assert [row.content for row in other] == ["账号二的记忆"]


# ---- R7 复核队列 ----


async def test_review_queue_dedupes_and_filters_sensitive(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        first = await memory_reviews.queue_candidate(
            session,
            ACCOUNT,
            content="用户偏好每周五发周报",
            kind="preference",
            confidence=0.4,
            evidence_quote="以后都周五发",
        )
        duplicate = await memory_reviews.queue_candidate(
            session, ACCOUNT, content="用户偏好每周五发周报", kind="preference"
        )
        sensitive = await memory_reviews.queue_candidate(
            session, ACCOUNT, content="token: sk-abcdefghijklmnop"
        )
        await memory_service.write_memory(
            session, ACCOUNT, content="用户偏好每周一发周报", kind="preference"
        )
        near_existing = await memory_reviews.queue_candidate(
            session, ACCOUNT, content="用户偏好每周一发周报", kind="preference"
        )
        pending = await memory_reviews.list_reviews(session, ACCOUNT)
    assert first is not None
    assert duplicate is None and sensitive is None and near_existing is None
    assert [item.id for item in pending] == [first.id]
    assert pending[0].evidence_quote == "以后都周五发"


async def test_resolve_review_approve_and_reject(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        approve = await memory_reviews.queue_candidate(
            session, ACCOUNT, content="用户偏好中文", kind="preference", confidence=0.4
        )
        reject = await memory_reviews.queue_candidate(
            session, ACCOUNT, content="用户可能喜欢咖啡", kind="note"
        )
    assert approve is not None and reject is not None
    async with session_scope(factory) as session:
        memory = await memory_reviews.resolve_review(
            session, ACCOUNT, approve.id, approve=True, note="确认"
        )
    assert memory is not None
    assert memory.content == "用户偏好中文"
    assert memory.source_type == "manual"
    assert memory.source_id == approve.id
    async with session_scope(factory) as session:
        assert (
            await memory_reviews.resolve_review(
                session, ACCOUNT, reject.id, approve=False
            )
            is None
        )
        stats = await memory_service.memory_stats(session, ACCOUNT)
    assert stats["pending_reviews"] == 0
    async with session_scope(factory) as session:
        with pytest.raises(ConflictError):
            await memory_reviews.resolve_review(
                session, ACCOUNT, approve.id, approve=True
            )
        with pytest.raises(NotFoundError):
            await memory_reviews.resolve_review(
                session, OTHER, approve.id, approve=True
            )
