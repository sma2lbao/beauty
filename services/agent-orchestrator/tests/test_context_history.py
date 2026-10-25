"""services.history 纯函数:摘要块拼装与截断口径(P0 上下文压缩)。

覆盖点:
- 无摘要时保持旧口径(最近 HISTORY_MESSAGES_LIMIT 条、单条 HISTORY_MESSAGE_CHARS 字);
- 摘要是第一条 system 消息,顺序为「摘要 → 近期原文」;
- recent/filter_after 的边界:created_at 等于覆盖点的消息应被过滤掉。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.services import history

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _msg(role: str, content: str, *, created_at: datetime | None = None) -> ChatMessage:
    return ChatMessage(
        tenant_id="t-1",
        conversation_id="c-1",
        role=role,
        content=content,
        created_at=created_at or NOW,
    )


def _at(offset_seconds: int) -> datetime:
    return NOW + timedelta(seconds=offset_seconds)


def test_to_prompt_without_summary_keeps_old_window() -> None:
    """无摘要:最近 20 条、按时间顺序、无 system 消息(旧行为不变)。"""
    rows = [_msg("user", f"第{index}条") for index in range(25)]

    messages = history.to_prompt(rows)

    assert [item["content"] for item in messages] == [
        f"第{index}条" for index in range(5, 25)
    ]
    assert all(item["role"] == "user" for item in messages)


def test_to_prompt_truncates_each_message() -> None:
    long_content = "x" * (history.HISTORY_MESSAGE_CHARS + 500)

    messages = history.to_prompt([_msg("assistant", long_content)])

    assert messages == [
        {"role": "assistant", "content": "x" * history.HISTORY_MESSAGE_CHARS}
    ]


def test_to_prompt_prepends_summary_as_first_system_message() -> None:
    """摘要非空:作为第一条 system 消息插入,近期消息顺序不变。"""
    rows = [_msg("user", "最近一句", created_at=_at(2))]

    messages = history.to_prompt(rows, summary="  用户在建记忆模块  ")

    assert messages[0] == {
        "role": "system",
        "content": f"{history.SUMMARY_PREFIX}\n用户在建记忆模块",
    }
    assert messages[1:] == [{"role": "user", "content": "最近一句"}]


def test_to_prompt_blank_summary_is_ignored() -> None:
    messages = history.to_prompt([_msg("user", "a")], summary="  \n  ")

    assert [item["role"] for item in messages] == ["user"]


def test_to_prompt_summary_with_empty_rows_is_single_system_message() -> None:
    messages = history.to_prompt([], summary="只有摘要")

    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert "只有摘要" in messages[0]["content"]


def test_to_prompt_limit_override() -> None:
    rows = [_msg("user", str(index)) for index in range(5)]

    messages = history.to_prompt(rows, limit=2)

    assert [item["content"] for item in messages] == ["3", "4"]


def test_recent_default_and_override() -> None:
    rows = [_msg("user", str(index)) for index in range(30)]

    assert [row.content for row in history.recent(rows)] == [
        str(index) for index in range(10, 30)
    ]
    assert [row.content for row in history.recent(rows, limit=2)] == ["28", "29"]
    assert history.recent([]) == []


def test_filter_after_none_returns_all_rows() -> None:
    rows = [_msg("user", "a"), _msg("assistant", "b")]

    assert history.filter_after(rows, None) == rows


def test_filter_after_drops_messages_at_or_before_covered_point() -> None:
    """created_at 等于覆盖点的消息已被摘要代表,必须过滤(边界)。"""
    covered_until = _at(10)
    rows = [
        _msg("user", "较早", created_at=_at(5)),
        _msg("assistant", "等于覆盖点", created_at=covered_until),
        _msg("user", "之后", created_at=_at(11)),
    ]

    kept = history.filter_after(rows, covered_until)

    assert [row.content for row in kept] == ["之后"]


def test_filter_after_skips_rows_without_created_at() -> None:
    row = _msg("user", "没有时间戳")
    row.created_at = None  # type: ignore[assignment]

    assert history.filter_after([row], NOW) == []
