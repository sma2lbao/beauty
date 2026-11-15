"""P3-C(R9-3/R10)单测:重嵌入维护、批量导入、远程 provider 契约。

与 test_memory_store.py 同口径:临时 sqlite 文件库 + create_all,服务函数显式
传参,零网络(远程 provider 全部走 httpx.MockTransport)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from common.errors import AppError
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services.embeddings import EmbeddingUnavailable
from agent_orchestrator.services.memory import store as memory_store
from agent_orchestrator.services.memory_remote import (
    MemoryProviderUnavailable,
    RemoteMemoryProvider,
)
from tests.conftest import _settings

ACCOUNT = "t-1"


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = _settings(tmp_path, "memory-import.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


# ---- R9-3 重嵌入 ----


async def test_reembed_rebuilds_outdated_vectors(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        row = Memory(
            account_id=ACCOUNT,
            content="用户偏好周报用表格",
            kind="preference",
            embedding=[0.1, 0.2],
            embedding_model="legacy-embed",
        )
        session.add(row)
        await session.flush()
        memory_id = row.id
    async with session_scope(factory) as session:
        preview = await memory_service.reembed_memories(session, ACCOUNT, dry_run=True)
        assert preview["scanned"] == 1
        assert preview["updated"] == 0
    async with session_scope(factory) as session:
        result = await memory_service.reembed_memories(session, ACCOUNT)
        assert result["scanned"] == 1
        assert result["updated"] == 1
        assert result["skipped"] == 0
    async with session_scope(factory) as session:
        rebuilt = await session.get(Memory, memory_id)
        assert rebuilt is not None
        assert rebuilt.embedding_model == result["model"]
        assert len(rebuilt.embedding or []) == 256
        # 重建是维护动作,不该刷新 updated_at/access_count。
        assert rebuilt.access_count == 0


async def test_reembed_reports_unavailable_embedding(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_scope(factory) as session:
        session.add(
            Memory(
                account_id=ACCOUNT,
                content="项目代号是蓝鲸",
                embedding=[0.1],
                embedding_model="legacy-embed",
            )
        )
        await session.flush()

    async def _unavailable(texts: list[str], **_: object):
        raise EmbeddingUnavailable("down")

    # embed_many 由 store 子模块直接调用,替换门面属性不影响那里的查找。
    monkeypatch.setattr(memory_store, "embed_many", _unavailable)
    async with session_scope(factory) as session:
        with pytest.raises(AppError) as caught:
            await memory_service.reembed_memories(session, ACCOUNT)
    assert caught.value.code == "memory.embedding_unavailable"


# ---- R10-1 批量导入 ----


async def test_import_creates_and_skips_duplicates_and_invalid(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    items = [
        {"content": "用户偏好周报用表格", "kind": "preference"},
        {"content": "用户偏好周报用表格", "kind": "preference"},
        {"content": "项目代号是蓝鲸", "kind": "fact"},
        {"content": "项目代号是蓝鲸", "kind": "unknown"},
        {"content": "key: sk-abcdefghijklmnopqrstuvwx", "kind": "note"},
        {"content": "部署环境是首尔", "kind": "fact", "scope": "workspace"},
    ]
    async with session_scope(factory) as session:
        result = await memory_service.import_memories(session, ACCOUNT, items)
    assert result["received"] == 6
    assert result["created"] == 2
    assert result["skipped_duplicate"] == 1
    assert result["skipped_invalid"] == 3
    codes = {error["code"] for error in result["errors"]}
    assert "memory.invalid_kind" in codes
    assert "memory.sensitive_rejected" in codes
    assert "memory.workspace_required" in codes
    async with session_scope(factory) as session:
        rows = await memory_service.list_memories(session, ACCOUNT)
    assert {row.content for row in rows} == {"用户偏好周报用表格", "项目代号是蓝鲸"}


async def test_import_dry_run_writes_nothing(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    items = [{"content": "用户偏好简洁回答", "kind": "preference"}]
    async with session_scope(factory) as session:
        preview = await memory_service.import_memories(
            session, ACCOUNT, items, dry_run=True
        )
        assert preview["created"] == 1
        assert preview["dry_run"] is True
    async with session_scope(factory) as session:
        assert await memory_service.list_memories(session, ACCOUNT) == []


async def test_import_does_not_supersede_existing_fact(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        old = await memory_service.write_memory(
            session, ACCOUNT, content="用户偏好使用中文回复", kind="preference"
        )
        old_id = old.id
    async with session_scope(factory) as session:
        await memory_service.import_memories(
            session,
            ACCOUNT,
            [{"content": "用户偏好使用英文回复", "kind": "preference"}],
        )
    async with session_scope(factory) as session:
        stored = await memory_service.get_memory(session, ACCOUNT, old_id)
        assert stored.invalid_at is None
        assert stored.superseded_by is None


async def test_export_then_import_roundtrip(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await memory_service.write_memory(
            session, ACCOUNT, content="项目代号是蓝鲸", kind="fact"
        )
    async with session_scope(factory) as session:
        exported = await memory_service.export_memories(session, ACCOUNT)
    # 导出文件顶层带 exported_at,导入按 pydantic 忽略额外字段处理。
    items = [
        {
            "content": row.content,
            "kind": row.kind,
            "scope": row.scope,
            "injection_mode": row.injection_mode,
            "confidence": row.confidence,
        }
        for row in exported
    ]
    assert items[0]["content"] == "项目代号是蓝鲸"
    async with session_scope(factory) as session:
        result = await memory_service.import_memories(session, ACCOUNT, items)
    assert result["skipped_duplicate"] == 1


# ---- R10-2 远程 provider 契约 ----


def _client(handler, **kwargs: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


async def test_remote_provider_contract_roundtrip() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/v1/memories/recall":
            return httpx.Response(200, json={"block": "相关长期记忆:..."})
        if path == "/v1/memories/remember":
            return httpx.Response(201, json={"id": "m-1"})
        if path == "/v1/memories/m-1":
            return httpx.Response(204)
        return httpx.Response(404)

    client = _client(handler)
    try:
        provider = RemoteMemoryProvider(
            "http://memory.test/", client=client, internal_secret="secret"
        )
        assert (
            await provider.recall(
                None, ACCOUNT, "周报", workspace_id="w-1", limit=3, max_chars=500
            )
            == "相关长期记忆:..."
        )
        assert await provider.remember(None, ACCOUNT, "项目代号是蓝鲸") == "m-1"
        await provider.forget(None, ACCOUNT, "m-1")
        await provider.aclose()
        # 注入的 client 归调用方,provider.aclose 不应把它关掉。
        assert not client.is_closed
    finally:
        await client.aclose()
    recall_body = json.loads(seen[0].content.decode())
    assert recall_body == {
        "query": "周报",
        "workspace_id": "w-1",
        "limit": 3,
        "max_chars": 500,
    }
    assert seen[2].method == "DELETE"
    assert seen[2].url.path == "/v1/memories/m-1"


async def test_remote_provider_maps_failures_to_unavailable() -> None:
    def transport_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = _client(transport_error)
    provider = RemoteMemoryProvider("http://memory.test", client=client)
    try:
        with pytest.raises(MemoryProviderUnavailable):
            await provider.recall(None, ACCOUNT, "周报")
    finally:
        await client.aclose()

    def bad_status(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "down"})

    client = _client(bad_status)
    provider = RemoteMemoryProvider("http://memory.test", client=client)
    try:
        with pytest.raises(MemoryProviderUnavailable):
            await provider.remember(None, ACCOUNT, "项目代号是蓝鲸")
    finally:
        await client.aclose()

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nope": True})

    client = _client(malformed)
    provider = RemoteMemoryProvider("http://memory.test", client=client)
    try:
        with pytest.raises(MemoryProviderUnavailable):
            await provider.recall(None, ACCOUNT, "周报")
    finally:
        await client.aclose()


def test_build_memory_provider_defaults_to_local(tmp_path: Path) -> None:
    local = memory_service.build_memory_provider()
    assert isinstance(local, memory_service.LocalMemoryProvider)
    settings = _settings(tmp_path, "memory-provider.db")
    configured = memory_service.build_memory_provider(settings)
    assert isinstance(configured, memory_service.LocalMemoryProvider)
    remote_settings = settings.model_copy(
        update={
            "memory_provider": "remote",
            "memory_remote_base_url": "http://memory.test",
        }
    )
    remote = memory_service.build_memory_provider(remote_settings)
    assert isinstance(remote, RemoteMemoryProvider)
