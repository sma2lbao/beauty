"""长期记忆存储层单测:本地嵌入、对话摘要、写入与校验。

纯函数(嵌入/切词)不碰库;库相关用例用临时 sqlite 文件库,建表走
Base.metadata.create_all(与既有测试一致)。服务函数全部显式传参,不依赖
Settings,所以这里不需要额外钉开关——数据库与记忆能力都不出网。列表/检索
与召回块见 test_memory_store_query.py,软删除/过期与 provider 见
test_memory_store_lifecycle.py。
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from common.errors import AppError
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.repositories import (
    ConversationSummaryRepository,
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
        defaulted = await memory_service.write_memory(
            session,
            ACCOUNT,
            content="默认有效期记忆",
            default_ttl_days=30,
        )
        overridden = await memory_service.write_memory(
            session,
            ACCOUNT,
            content="显式有效期记忆",
            ttl_days=3,
            default_ttl_days=30,
        )
    assert temporary.expires_at is not None
    assert permanent.expires_at is None
    # None = 部署默认;显式值优先于默认值
    assert defaulted.expires_at is not None
    assert overridden.expires_at is not None
    assert overridden.expires_at < defaulted.expires_at


async def test_write_memory_rejects_invalid_input(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cases: list[tuple[dict[str, object], str]] = [
        ({"content": "   "}, "memory.empty"),
        ({"content": "x", "kind": "unknown"}, "memory.invalid_kind"),
        ({"content": "x", "scope": "team"}, "memory.invalid_scope"),
        ({"content": "x", "source_type": "api"}, "memory.invalid_source"),
        ({"content": "x", "scope": "workspace"}, "memory.workspace_required"),
        ({"content": "x", "ttl_days": -1}, "memory.invalid_ttl"),
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
