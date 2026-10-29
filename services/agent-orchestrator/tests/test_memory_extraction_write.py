"""对话记忆自动抽取与巩固单测:解析宽容、去重写入、对话抽取与近重复合并。

LLM 全部用本文件的小假客户端(零网络);库相关用例用临时 sqlite 文件库建表
(与 test_memory_store 一致)。设置对象用 SimpleNamespace 构造:conftest 默认把
自动抽取/巩固开关关掉,这里显式打开,不依赖进程环境。
本文件承载原 ``test_memory_extraction.py`` 拆分后的去重写入、对话抽取与巩固部分。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.memory import Memory
from agent_orchestrator.repositories import MemoryRepository, MessageRepository
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services.llm import LLMReply
from agent_orchestrator.services.memory_extraction import (
    ExtractedMemory,
    consolidate,
    extract_from_messages,
    remember_candidates,
)
from tests.conftest import _settings

#: 记忆服务只认 tenant_id 字符串;与 conftest 的 bearer 租户保持一致。
TENANT = "t-1"
OTHER = "t-2"


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose,避免句柄残留。"""
    settings = _settings(tmp_path, "memory-extraction.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


class FakeLLM:
    """最小 LLMClient:记录收到的 messages,返回固定文本或抛错。"""

    def __init__(self, reply: str = "", *, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMReply:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return LLMReply(content=self.reply)


def _extraction_settings(**overrides: Any) -> SimpleNamespace:
    """一次抽取所需的全部开关与上限;默认全开,便于单测直接走主路径。"""
    values: dict[str, Any] = {
        "memory_enabled": True,
        "memory_auto_extract_enabled": True,
        "memory_extraction_max_chars": 1200,
        "memory_extraction_max_items": 5,
        "memory_max_content_chars": 8000,
        "memory_max_entries_per_tenant": 2000,
        "memory_dedupe_threshold": 0.9,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _chat_message(role: str, content: str) -> ChatMessage:
    return ChatMessage(
        tenant_id=TENANT,
        conversation_id="c-1",
        role=role,
        content=content,
    )


# ---- 去重写入 ----


async def test_remember_candidates_writes_and_dedupes(factory: Any) -> None:
    async with session_scope(factory) as session:
        first = await remember_candidates(
            session,
            TENANT,
            candidates=[ExtractedMemory(kind="preference", content="用户偏好简洁回答")],
            source_type="chat",
            source_id="c-1",
        )
    assert len(first) == 1
    assert first[0].source_type == "chat"
    assert first[0].source_id == "c-1"

    async with session_scope(factory) as session:
        again = await remember_candidates(
            session,
            TENANT,
            candidates=[
                ExtractedMemory(
                    kind="preference",
                    content="用户偏好简洁回答,不要长篇大论",
                )
            ],
        )
        assert again == []
        stats = await memory_service.memory_stats(session, TENANT)
    assert stats["active"] == 1


async def test_remember_candidates_keeps_pinned_memory(factory: Any) -> None:
    async with session_scope(factory) as session:
        pinned = await memory_service.write_memory(
            session,
            TENANT,
            content="用户偏好简洁回答",
            kind="preference",
            pinned=True,
        )

    async with session_scope(factory) as session:
        written = await remember_candidates(
            session,
            TENANT,
            candidates=[ExtractedMemory(kind="preference", content="用户偏好简洁回答")],
        )
        assert written == []
        kept = await session.get(Memory, pinned.id)
        stats = await memory_service.memory_stats(session, TENANT)
    assert kept is not None
    assert kept.pinned is True
    assert kept.deleted_at is None
    assert stats["active"] == 1


async def test_remember_candidates_separates_kind_and_workspace(
    factory: Any,
) -> None:
    async with session_scope(factory) as session:
        written = await remember_candidates(
            session,
            TENANT,
            candidates=[
                ExtractedMemory(kind="preference", content="用户偏好简洁回答"),
                ExtractedMemory(kind="fact", content="用户偏好简洁回答"),
                ExtractedMemory(
                    kind="preference",
                    content="用户偏好简洁回答",
                    scope="workspace",
                ),
            ],
            workspace_id="ws-1",
        )
        stats = await memory_service.memory_stats(session, TENANT)
    # 同内容但 kind/工作区不同:语义角色不同,不能互相去重
    assert len(written) == 3
    assert stats["active"] == 3


async def test_remember_candidates_skips_workspace_without_id(factory: Any) -> None:
    async with session_scope(factory) as session:
        written = await remember_candidates(
            session,
            TENANT,
            candidates=[
                ExtractedMemory(kind="fact", content="工作区事实", scope="workspace")
            ],
        )
        stats = await memory_service.memory_stats(session, TENANT)
    assert written == []
    assert stats["active"] == 0


async def test_remember_candidates_stops_at_entry_limit(factory: Any) -> None:
    async with session_scope(factory) as session:
        first = await remember_candidates(
            session,
            TENANT,
            candidates=[ExtractedMemory(kind="note", content="已有记忆")],
            max_entries=1,
        )
        second = await remember_candidates(
            session,
            TENANT,
            candidates=[
                ExtractedMemory(kind="note", content="第二条"),
                ExtractedMemory(kind="note", content="第三条"),
            ],
            max_entries=1,
        )
        stats = await memory_service.memory_stats(session, TENANT)
    assert len(first) == 1
    assert second == []
    assert stats["active"] == 1


async def test_remember_candidates_empty_returns_empty(factory: Any) -> None:
    async with session_scope(factory) as session:
        assert await remember_candidates(session, TENANT, candidates=[]) == []


# ---- 对话 → 抽取 → 落库 ----


async def test_extract_from_messages_writes_memories(factory: Any) -> None:
    llm = FakeLLM(
        json.dumps(
            {"memories": [{"kind": "preference", "content": "用户偏好简洁回答"}]}
        )
    )
    async with session_scope(factory) as session:
        written = await extract_from_messages(
            session,
            llm,
            tenant_id=TENANT,
            messages=[
                _chat_message("user", "记住:回答简洁点"),
                _chat_message("assistant", "好的,以后简洁回答"),
            ],
            source_id="c-1",
            settings=_extraction_settings(),
        )
        stats = await memory_service.memory_stats(session, TENANT)
    assert len(written) == 1
    assert written[0].source_type == "chat"
    assert written[0].source_id == "c-1"
    assert stats["active"] == 1
    transcript = llm.calls[0][1]["content"]
    assert "user: 记住:回答简洁点" in transcript
    assert "assistant: 好的,以后简洁回答" in transcript


async def test_extract_from_messages_respects_switches(factory: Any) -> None:
    llm = FakeLLM(json.dumps([{"kind": "note", "content": "x"}]))
    messages = [_chat_message("user", "记住 x")]
    async with session_scope(factory) as session:
        for settings in (
            _extraction_settings(memory_auto_extract_enabled=False),
            _extraction_settings(memory_enabled=False),
        ):
            assert (
                await extract_from_messages(
                    session,
                    llm,
                    tenant_id=TENANT,
                    messages=messages,
                    settings=settings,
                )
                == []
            )
    # 开关关闭时不应白调一次模型
    assert llm.calls == []


async def test_extract_from_messages_truncates_transcript(factory: Any) -> None:
    llm = FakeLLM("[]")
    async with session_scope(factory) as session:
        await extract_from_messages(
            session,
            llm,
            tenant_id=TENANT,
            messages=[
                _chat_message("user", "很长的老消息" * 10),
                _chat_message("assistant", "最新回复"),
            ],
            settings=_extraction_settings(memory_extraction_max_chars=20),
        )
    transcript = llm.calls[0][1]["content"]
    # 预算从最新往回取:旧的长消息被丢掉,最新一句完整保留
    assert len(transcript) <= 20
    assert "assistant: 最新回复" in transcript


async def test_extract_from_messages_applies_max_items(factory: Any) -> None:
    payload = json.dumps(
        [{"kind": "fact", "content": f"事实{index}"} for index in range(5)]
    )
    llm = FakeLLM(payload)
    async with session_scope(factory) as session:
        written = await extract_from_messages(
            session,
            llm,
            tenant_id=TENANT,
            messages=[_chat_message("user", "记住几个事实")],
            settings=_extraction_settings(memory_extraction_max_items=2),
        )
    assert len(written) == 2


# ---- 周期巩固 ----


async def test_consolidate_prefers_pinned_memory(factory: Any) -> None:
    async with session_scope(factory) as session:
        pinned = await memory_service.write_memory(
            session,
            TENANT,
            content="用户偏好简洁回答",
            kind="preference",
            pinned=True,
        )
        plain = await memory_service.write_memory(
            session,
            TENANT,
            content="用户偏好简洁回答,不要长篇大论",
            kind="preference",
        )
        # 哪怕普通记忆访问更多,pinned 也不能被合并掉
        plain.access_count = 9
        await session.flush()
        removed = await consolidate(session, TENANT)
        refreshed_pinned = await session.get(Memory, pinned.id)
        refreshed_plain = await session.get(Memory, plain.id)
    assert removed == 1
    assert refreshed_pinned is not None
    assert refreshed_pinned.deleted_at is None
    assert refreshed_plain is not None
    assert refreshed_plain.deleted_at is not None


async def test_consolidate_keeps_most_accessed_memory(factory: Any) -> None:
    async with session_scope(factory) as session:
        hot = await memory_service.write_memory(
            session, TENANT, content="项目代号是蓝鲸"
        )
        cold = await memory_service.write_memory(
            session, TENANT, content="项目代号是蓝鲸"
        )
        hot.access_count = 5
        await session.flush()
        removed = await consolidate(session, TENANT)
        refreshed_hot = await session.get(Memory, hot.id)
        refreshed_cold = await session.get(Memory, cold.id)
    assert removed == 1
    assert refreshed_hot is not None
    assert refreshed_hot.deleted_at is None
    assert refreshed_cold is not None
    assert refreshed_cold.deleted_at is not None


async def test_consolidate_keeps_earliest_memory(factory: Any) -> None:
    async with session_scope(factory) as session:
        older = await memory_service.write_memory(session, TENANT, content="回答要简洁")
        newer = await memory_service.write_memory(session, TENANT, content="回答要简洁")
        older.created_at = datetime.now(UTC) - timedelta(days=1)
        await session.flush()
        removed = await consolidate(session, TENANT)
        refreshed_older = await session.get(Memory, older.id)
        refreshed_newer = await session.get(Memory, newer.id)
    assert removed == 1
    assert refreshed_older is not None
    assert refreshed_older.deleted_at is None
    assert refreshed_newer is not None
    assert refreshed_newer.deleted_at is not None


async def test_consolidate_only_merges_same_kind_and_workspace(
    factory: Any,
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(
            session, TENANT, content="用户偏好简洁回答", kind="preference"
        )
        await memory_service.write_memory(
            session, TENANT, content="用户偏好简洁回答", kind="fact"
        )
        await memory_service.write_memory(
            session, TENANT, content="用户偏好简洁回答", kind="preference"
        )
        await memory_service.write_memory(
            session,
            TENANT,
            content="用户偏好简洁回答",
            kind="preference",
            scope="workspace",
            workspace_id="ws-1",
        )
        # 只有 tenant 级的两条 preference 互为近重复;fact 与 workspace 各算一组
        assert await consolidate(session, TENANT) == 1


async def test_consolidate_does_not_merge_distinct_memories(factory: Any) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(session, TENANT, content="项目代号是蓝鲸")
        await memory_service.write_memory(session, TENANT, content="回答要简洁")
        assert await consolidate(session, TENANT) == 0


async def test_consolidate_zero_for_single_or_empty(factory: Any) -> None:
    async with session_scope(factory) as session:
        assert await consolidate(session, TENANT) == 0
        await memory_service.write_memory(session, TENANT, content="唯一记忆")
        assert await consolidate(session, TENANT) == 0


# ---- 仓储新增方法 ----


async def test_message_repository_counts_messages(factory: Any) -> None:
    async with session_scope(factory) as session:
        conversation = Conversation(tenant_id=TENANT, title="测试对话")
        session.add(conversation)
        await session.flush()
        for index in range(2):
            session.add(
                ChatMessage(
                    tenant_id=TENANT,
                    conversation_id=conversation.id,
                    role="user",
                    content=f"消息{index}",
                )
            )
        await session.flush()
        repository = MessageRepository(session)
        assert await repository.count_for_conversation(TENANT, conversation.id) == 2
        assert await repository.count_for_conversation(OTHER, conversation.id) == 0
        assert await repository.count_for_conversation(TENANT, "missing") == 0


async def test_memory_repository_tenant_ids_with_active(factory: Any) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(session, TENANT, content="租户一的记忆")
        await memory_service.write_memory(session, OTHER, content="租户二的记忆")
        deleted = await memory_service.write_memory(session, "t-3", content="将被删除")
        await memory_service.delete_memory(session, "t-3", deleted.id)
        expired = await memory_service.write_memory(
            session, "t-4", content="将过期", ttl_days=1
        )
        expired.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.flush()
        tenant_ids = await MemoryRepository(session).tenant_ids_with_active()
    # 软删/过期的租户没有"生效中"记忆,不该进扫描清单
    assert tenant_ids == [TENANT, OTHER]
