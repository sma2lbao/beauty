"""对话记忆自动抽取与巩固单测:解析宽容、去重写入、对话抽取与近重复合并。

LLM 全部用本文件的小假客户端(零网络);库相关用例用临时 sqlite 文件库建表
(与 test_memory_store 一致)。设置对象用 SimpleNamespace 构造:conftest 默认把
自动抽取/巩固开关关掉,这里显式打开,不依赖进程环境。
本文件承载原 ``test_memory_extraction.py`` 拆分后的候选解析与 LLM 抽取部分。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.services.llm import LLMReply
from agent_orchestrator.services.memory_extraction import (
    EXTRACTION_SYSTEM_PROMPT,
    extract_candidates,
    parse_candidates,
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


# ---- 候选解析(纯函数) ----


def test_parse_candidates_accepts_fenced_json_with_noise() -> None:
    raw_reply = (
        "好的,结果如下:\n"
        '```json\n[{"kind": "preference", "content": " 用户偏好简洁回答 ", '
        '"scope": "workspace"}]\n```\n以上。'
    )
    candidates = parse_candidates(raw_reply)
    assert [(item.kind, item.content, item.scope) for item in candidates] == [
        ("preference", "用户偏好简洁回答", "workspace")
    ]

    # 没有围栏时退化为"取最外层方括号之间的片段"
    noisy = '结果: [{"kind": "fact", "content": "项目代号是蓝鲸"}] 完毕'
    assert [item.content for item in parse_candidates(noisy)] == ["项目代号是蓝鲸"]


def test_parse_candidates_normalizes_and_drops_invalid() -> None:
    raw_reply = json.dumps(
        {
            "memories": [
                {"kind": "unknown", "content": "项目代号是蓝鲸", "scope": "team"},
                {"kind": "fact", "content": "项目代号是蓝鲸   "},
                "free text",
                {"kind": "fact"},
                {"kind": "fact", "content": "回答要简洁"},
            ]
        }
    )
    candidates = parse_candidates(raw_reply)
    assert [(item.kind, item.content, item.scope) for item in candidates] == [
        ("note", "项目代号是蓝鲸", "tenant"),
        ("fact", "回答要简洁", "tenant"),
    ]


def test_parse_candidates_respects_limits() -> None:
    raw_reply = json.dumps(
        [{"kind": "fact", "content": f"事实{index}"} for index in range(10)]
    )
    assert len(parse_candidates(raw_reply, max_items=3)) == 3

    truncated = parse_candidates(
        json.dumps([{"kind": "fact", "content": "长" * 20}]),
        max_content_chars=5,
    )
    assert truncated[0].content == "长" * 5

    assert parse_candidates("这不是 JSON") == []
    assert parse_candidates("") == []
    assert parse_candidates(raw_reply, max_items=0) == []


# ---- LLM 抽取 ----


async def test_extract_candidates_prompts_system_and_parses_reply() -> None:
    llm = FakeLLM(json.dumps([{"kind": "fact", "content": "项目代号是蓝鲸"}]))
    candidates = await extract_candidates(llm, "user: 记住项目代号是蓝鲸")
    assert [item.content for item in candidates] == ["项目代号是蓝鲸"]
    messages = llm.calls[0]
    assert messages[0] == {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "user: 记住项目代号是蓝鲸"}


async def test_extract_candidates_swallows_llm_failure() -> None:
    llm = FakeLLM(error=RuntimeError("模型挂了"))
    assert await extract_candidates(llm, "user: 你好") == []


async def test_extract_candidates_skips_llm_without_transcript() -> None:
    llm = FakeLLM()
    assert await extract_candidates(llm, "   ") == []
    assert llm.calls == []
