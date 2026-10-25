"""services.chat.prompt:压缩摘要与长期记忆召回的上下文注入(P0/P1 记忆模块)。

只验证装配口径:memory_block 拼进 system、summary 经 history.to_prompt 变成
第一条 system、context 为空时退回"从 DB 读近期原文"的旧行为。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.services import chat

NOW = datetime(2026, 10, 25, 12, 0)


def _msg(role: str, content: str, *, created_at: datetime | None = None) -> ChatMessage:
    return ChatMessage(
        tenant_id="t-1",
        conversation_id="c-1",
        role=role,
        content=content,
        created_at=created_at or NOW,
    )


@asynccontextmanager
async def _session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/chat.db")
    try:
        async with factory.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_scope(factory) as db_session:
            yield db_session
    finally:
        await factory.engine.dispose()


async def test_prompt_with_context_injects_memory_block_and_summary(
    tmp_path: Path,
) -> None:
    memory_block = "相关长期记忆:\n- 用户偏好暗色主题"
    async with _session(tmp_path) as session:
        message = _msg("user", "现在的问题", created_at=NOW + timedelta(seconds=2))
        messages = await chat.prompt(
            session,
            message,
            context=chat.ChatPromptContext(
                rows=[_msg("assistant", "之前的回答", created_at=NOW)],
                summary="更早的摘要",
                memory_block=memory_block,
            ),
        )

    # 主 system:CHAT_SYSTEM_PROMPT + 记忆块
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == f"{chat.CHAT_SYSTEM_PROMPT}\n\n{memory_block}"
    # 摘要块:history.to_prompt 插入的第一条 system
    assert messages[1]["role"] == "system"
    assert "更早的摘要" in messages[1]["content"]
    assert messages[2] == {"role": "assistant", "content": "之前的回答"}
    assert messages[-1] == {"role": "user", "content": "现在的问题"}


async def test_prompt_with_empty_rows_has_only_system_and_user(tmp_path: Path) -> None:
    async with _session(tmp_path) as session:
        message = _msg("user", "第一句")
        messages = await chat.prompt(
            session,
            message,
            context=chat.ChatPromptContext(memory_block="记忆块"),
        )

    assert [item["role"] for item in messages] == ["system", "user"]
    assert messages[0]["content"] == f"{chat.CHAT_SYSTEM_PROMPT}\n\n记忆块"
    assert messages[1]["content"] == "第一句"


async def test_prompt_without_memory_or_summary_keeps_plain_system(
    tmp_path: Path,
) -> None:
    """context 内容为空:system 保持 CHAT_SYSTEM_PROMPT(不追加空块)。"""
    async with _session(tmp_path) as session:
        messages = await chat.prompt(
            session, _msg("user", "你好"), context=chat.ChatPromptContext()
        )

    assert messages[0] == {"role": "system", "content": chat.CHAT_SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "你好"}


async def test_prompt_puts_memory_before_reference_note(tmp_path: Path) -> None:
    async with _session(tmp_path) as session:
        messages = await chat.prompt(
            session,
            _msg("user", "问题"),
            reference_note="本次引用的 MCP server:demo",
            context=chat.ChatPromptContext(memory_block="记忆块"),
        )

    system = messages[0]["content"]
    assert system.startswith(chat.CHAT_SYSTEM_PROMPT)
    assert system.index("记忆块") < system.index("本次引用的 MCP server:demo")


async def test_prompt_without_context_reads_recent_history_from_db(
    tmp_path: Path,
) -> None:
    """context=None:保持旧口径——从 DB 取当前消息之前的全部轮次。"""
    async with _session(tmp_path) as session:
        conversation = Conversation(tenant_id="t-1", title="历史")
        session.add(conversation)
        await session.flush()
        first = ChatMessage(
            tenant_id="t-1",
            conversation_id=conversation.id,
            role="user",
            content="第一句",
            created_at=NOW,
        )
        current = ChatMessage(
            tenant_id="t-1",
            conversation_id=conversation.id,
            role="user",
            content="现在是",
            created_at=NOW + timedelta(seconds=1),
        )
        session.add_all([first, current])
        await session.flush()

        messages = await chat.prompt(session, current)

    assert [item["role"] for item in messages] == ["system", "user", "user"]
    assert messages[1]["content"] == "第一句"
    assert messages[2]["content"] == "现在是"
