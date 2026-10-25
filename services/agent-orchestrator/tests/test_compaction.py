"""services.compaction:token 估算、摘要器、压缩折叠与失败降级。

纯函数部分不碰库;compact_history 的库相关用例用临时 sqlite +
ConversationSummaryRepository(建表走 Base.metadata.create_all,与既有测试一致)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.repositories import ConversationSummaryRepository
from agent_orchestrator.services import compaction
from tests.conftest import FakeLLM

#: 与 sqlite 读回的 naive datetime 同口径(真实链路里 rows 也是 ORM 装载的 naive 值)
NOW = datetime(2026, 10, 25, 12, 0)


def _msg(role: str, content: str, *, created_at: datetime | None = None) -> ChatMessage:
    return ChatMessage(
        tenant_id="t-1",
        conversation_id="c-1",
        role=role,
        content=content,
        created_at=created_at or NOW,
    )


def _rows(count: int, *, start: int = 0) -> list[ChatMessage]:
    """一组时间递增的消息(压缩输入);内容短,便于阈值控制。"""
    return [
        _msg("user", f"消息{index}", created_at=NOW + timedelta(seconds=start + index))
        for index in range(count)
    ]


@asynccontextmanager
async def _session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/compaction.db")
    try:
        async with factory.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_scope(factory) as db_session:
            yield db_session
    finally:
        await factory.engine.dispose()


# ---- 纯函数:estimate_tokens ----


def test_estimate_tokens_empty_is_zero() -> None:
    assert compaction.estimate_tokens("") == 0
    # 仅空白不是空串:按长度估算会得到 1(阈值判断不依赖这个边界)
    assert compaction.estimate_tokens("   ") == 1


def test_estimate_tokens_non_empty_at_least_one() -> None:
    assert compaction.estimate_tokens("a") == 1
    assert compaction.estimate_tokens("ab") == 1
    assert compaction.estimate_tokens("a" * 100) == 50


# ---- 纯函数:summarize ----


async def test_summarize_puts_previous_summary_and_new_rows_into_user_prompt() -> None:
    fake = FakeLLM(["合并后的摘要"])

    summary = await compaction.summarize(
        fake,
        previous_summary="旧的决定:用 sqlite",
        rows=[_msg("user", "新增一句")],
        max_chars=100,
    )

    assert summary == "合并后的摘要"
    system, user = fake.calls[0]
    assert system == {
        "role": "system",
        "content": compaction.SUMMARY_SYSTEM_PROMPT,
    }
    assert user["role"] == "user"
    content = user["content"]
    assert "【已有摘要】" in content
    assert "旧的决定:用 sqlite" in content
    assert "【新增待压缩对话】" in content
    assert "user: 新增一句" in content
    assert content.index("【已有摘要】") < content.index("【新增待压缩对话】")


async def test_summarize_without_previous_summary_has_no_existing_block() -> None:
    fake = FakeLLM(["摘要"])

    await compaction.summarize(
        fake, previous_summary="   ", rows=[_msg("user", "新内容")], max_chars=100
    )

    assert "【已有摘要】" not in fake.calls[0][1]["content"]
    assert "【新增待压缩对话】" in fake.calls[0][1]["content"]


async def test_summarize_truncates_input_to_max_input_chars() -> None:
    fake = FakeLLM(["摘要"])
    rows = [_msg("user", "x" * 5000)]

    await compaction.summarize(
        fake, previous_summary="", rows=rows, max_chars=100, max_input_chars=50
    )

    assert len(fake.calls[0][1]["content"]) == 50


async def test_summarize_truncates_output_to_max_chars() -> None:
    fake = FakeLLM(["y" * 100])

    summary = await compaction.summarize(
        fake, previous_summary="", rows=[_msg("user", "内容")], max_chars=10
    )

    assert summary == "y" * 10


async def test_summarize_strips_whitespace_around_reply() -> None:
    fake = FakeLLM(["  有空格  "])

    summary = await compaction.summarize(
        fake, previous_summary="", rows=[_msg("user", "内容")], max_chars=100
    )

    assert summary == "有空格"


async def test_summarize_raises_on_empty_reply() -> None:
    fake = FakeLLM(["   "])

    with pytest.raises(ValueError, match="compaction.summarizer_empty"):
        await compaction.summarize(
            fake, previous_summary="", rows=[_msg("user", "内容")], max_chars=100
        )


# ---- compact_history:前置条件与降级 ----


async def test_compact_history_requires_llm_and_rows(tmp_path: Path) -> None:
    async with _session(tmp_path) as session:
        rows = _rows(5)
        assert (
            await compaction.compact_history(
                session,
                "t-1",
                "c-1",
                None,
                rows=rows,
                threshold_tokens=0,
                keep_recent=1,
                summary_max_chars=100,
            )
            is None
        )
        assert (
            await compaction.compact_history(
                session,
                "t-1",
                "c-1",
                FakeLLM(["摘要"]),
                rows=[],
                threshold_tokens=0,
                keep_recent=1,
                summary_max_chars=100,
            )
            is None
        )


async def test_compact_history_skips_when_not_enough_rows(tmp_path: Path) -> None:
    fake = FakeLLM(["摘要"])
    async with _session(tmp_path) as session:
        result = await compaction.compact_history(
            session,
            "t-1",
            "c-1",
            fake,
            rows=_rows(3),
            threshold_tokens=0,
            keep_recent=3,
            summary_max_chars=100,
        )

        assert result is None
        assert fake.calls == []


async def test_compact_history_skips_below_token_threshold(tmp_path: Path) -> None:
    fake = FakeLLM(["摘要"])
    async with _session(tmp_path) as session:
        result = await compaction.compact_history(
            session,
            "t-1",
            "c-1",
            fake,
            rows=_rows(5),
            threshold_tokens=10_000,
            keep_recent=1,
            summary_max_chars=100,
        )

        assert result is None
        assert fake.calls == []


async def test_compact_history_folds_old_rows_and_persists_summary(
    tmp_path: Path,
) -> None:
    fake = FakeLLM(["折叠后的摘要"])
    rows = _rows(5, start=1)
    async with _session(tmp_path) as session:
        result = await compaction.compact_history(
            session,
            "t-1",
            "c-1",
            fake,
            rows=rows,
            threshold_tokens=1,
            keep_recent=1,
            summary_max_chars=100,
        )

        assert result is not None
        assert result.summary == "折叠后的摘要"
        # 折叠 fresh[:-keep_recent] = 前 4 条,覆盖点取最后一条被折叠消息的时间
        assert result.covered_until == rows[3].created_at
        assert result.covered_messages == 4
        assert result.tokens_before > result.tokens_after

        stored = await ConversationSummaryRepository(session).get_for_conversation(
            "t-1", "c-1"
        )
        assert stored is not None
        assert stored.summary == "折叠后的摘要"
        assert stored.covered_until == rows[3].created_at
        assert stored.covered_messages == 4
        assert len(fake.calls) == 1
        assert "user: 消息0" in fake.calls[0][1]["content"]


async def test_compact_history_accumulates_covered_messages(tmp_path: Path) -> None:
    """已有摘要时只折叠覆盖点之后的消息,covered_messages 累加。"""
    repo = ConversationSummaryRepository
    rows = _rows(6, start=1)
    fake = FakeLLM(["新摘要"])
    async with _session(tmp_path) as session:
        await repo(session).upsert(
            "t-1",
            "c-1",
            summary="旧摘要",
            covered_until=NOW + timedelta(seconds=2),
            covered_messages=2,
            tokens_before=10,
            tokens_after=5,
        )
        result = await compaction.compact_history(
            session,
            "t-1",
            "c-1",
            fake,
            rows=rows,
            threshold_tokens=1,
            keep_recent=1,
            summary_max_chars=100,
        )

        assert result is not None
        # fresh = 覆盖点之后 4 条(created_at +3..+6),折叠前 3 条 → 2+3=5
        assert result.covered_messages == 5
        assert result.covered_until == rows[4].created_at
        assert "【已有摘要】" in fake.calls[0][1]["content"]
        assert "旧摘要" in fake.calls[0][1]["content"]


async def test_compact_history_degrades_when_summarizer_raises(tmp_path: Path) -> None:
    fake = FakeLLM(["摘要"], fail_on_call=1)
    async with _session(tmp_path) as session:
        result = await compaction.compact_history(
            session,
            "t-1",
            "c-1",
            fake,
            rows=_rows(5),
            threshold_tokens=1,
            keep_recent=1,
            summary_max_chars=100,
        )

        assert result is None
        stored = await ConversationSummaryRepository(session).get_for_conversation(
            "t-1", "c-1"
        )
        assert stored is None


async def test_compact_history_degrades_on_empty_summarizer_output(
    tmp_path: Path,
) -> None:
    fake = FakeLLM([""])
    async with _session(tmp_path) as session:
        result = await compaction.compact_history(
            session,
            "t-1",
            "c-1",
            fake,
            rows=_rows(5),
            threshold_tokens=1,
            keep_recent=1,
            summary_max_chars=100,
        )

        assert result is None
        stored = await ConversationSummaryRepository(session).get_for_conversation(
            "t-1", "c-1"
        )
        assert stored is None
