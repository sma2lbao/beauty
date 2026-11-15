"""P1-B 压缩挂点:PreCompact 的 skip 跳过摘要器,allow 照常压缩。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from database.base import Base
from database.session import create_session_factory, session_scope

from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.models.hook import AccountHook
from agent_orchestrator.repositories import HookRepository
from agent_orchestrator.services import compaction
from agent_orchestrator.services import hooks as hooks_service
from tests.conftest import FakeLLM, _settings
from tests.support.hooks_mounts import HOOK_TARGET, _HookServer, _resolver


def _messages(count: int) -> list[ChatMessage]:
    now = datetime(2026, 11, 1, 12, 0)
    return [
        ChatMessage(
            account_id="t-1",
            conversation_id="c-1",
            role="user",
            content=f"消息{index}",
            created_at=now + timedelta(seconds=index),
        )
        for index in range(count)
    ]


@asynccontextmanager
async def _db_session(tmp_path: Path, settings: Any) -> AsyncIterator[Any]:
    factory = create_session_factory(settings.database_url)
    try:
        async with factory.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_scope(factory) as session:
            yield session
    finally:
        await factory.engine.dispose()


async def _compact(
    tmp_path: Path, settings: Any, fake: FakeLLM
) -> tuple[Any, list[Any]]:
    async with _db_session(tmp_path, settings) as session:
        result = await compaction.compact_history(
            session,
            "t-1",
            "c-1",
            fake,
            rows=_messages(6),
            threshold_tokens=1,
            keep_recent=2,
            summary_max_chars=100,
        )
        return result, fake.calls


def _seed_hook(settings: Any, tmp_path: Path, event: str) -> None:
    async def seed() -> None:
        async with _db_session(tmp_path, settings) as session:
            await HookRepository(session).add(
                AccountHook(
                    account_id="t-1",
                    event=event,
                    matcher="*",
                    target=HOOK_TARGET,
                    secret="s3cr3t",
                    timeout_seconds=3,
                    enabled=True,
                )
            )

    asyncio.run(seed())


def test_pre_compact_skip_returns_none_and_skips_summarizer(tmp_path: Path) -> None:
    server = _HookServer(
        lambda body, index: {"decision": "skip", "reason": "本轮不压缩"}
    )
    settings = _settings(tmp_path, "compact-skip.db", hooks_enabled=True)
    hooks_service.configure_hooks(
        settings, transport=httpx.MockTransport(server), resolver=_resolver
    )
    try:
        _seed_hook(settings, tmp_path, "PreCompact")
        fake = FakeLLM(["不应被调用"])
        result, calls = asyncio.run(_compact(tmp_path, settings, fake))
    finally:
        hooks_service.reset_hooks()

    assert result is None
    assert calls == []
    assert server.bodies[0]["event"] == "PreCompact"
    assert server.bodies[0]["conversation_id"] == "c-1"
    assert server.bodies[0]["tokens_before"] > 1
    assert server.bodies[0]["reason"] == "threshold"


def test_pre_compact_allow_still_compacts(tmp_path: Path) -> None:
    server = _HookServer(lambda body, index: {"decision": "allow"})
    settings = _settings(tmp_path, "compact-allow.db", hooks_enabled=True)
    hooks_service.configure_hooks(
        settings, transport=httpx.MockTransport(server), resolver=_resolver
    )
    try:
        _seed_hook(settings, tmp_path, "PreCompact")
        fake = FakeLLM(["这是摘要"])
        result, calls = asyncio.run(_compact(tmp_path, settings, fake))
    finally:
        hooks_service.reset_hooks()

    assert result is not None
    assert result.summary == "这是摘要"
    assert len(calls) == 1
    assert server.bodies[0]["event"] == "PreCompact"
