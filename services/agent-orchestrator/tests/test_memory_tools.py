"""记忆工具单测:handler 从 ToolContext 取账号/工作区/来源,自建事务。

直接调用 ToolSpec.handler(绕过 ToolRegistry),覆盖参数校验、上下文缺失、
来源落库与账号隔离;临时 sqlite 文件库,建表走 Base.metadata.create_all。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.services.memory_tools import build_memory_tools
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk, ToolSpec
from tests.conftest import _settings

ACCOUNT = "t-1"
OTHER = "t-2"


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose。"""
    settings = _settings(tmp_path, "memory-tools.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


def _tools(
    factory: async_sessionmaker[AsyncSession],
) -> dict[str, ToolSpec]:
    return {tool.name: tool for tool in build_memory_tools(factory)}


async def test_memory_write_and_search_roundtrip(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tools = _tools(factory)
    context = ToolContext(
        account_id=ACCOUNT,
        run_id="run-1",
        conversation_id="conv-1",
        workspace_id="ws-1",
    )
    with bind_tool_context(context):
        written = json.loads(
            await tools["memory_write"].handler(
                {
                    "content": "用户偏好简洁回答",
                    "kind": "preference",
                    "scope": "workspace",
                }
            )
        )
        hits = json.loads(await tools["memory_search"].handler({"query": "简洁回答"}))
    assert set(written) == {"id"}
    assert hits["hits"][0]["id"] == written["id"]
    assert hits["hits"][0]["kind"] == "preference"
    assert hits["hits"][0]["content"] == "用户偏好简洁回答"

    async with session_scope(factory) as session:
        row = await session.get(Memory, written["id"])
    assert row is not None
    # run 上下文优先作为来源;工作区级记忆落在 ctx.workspace_id 下
    assert (row.source_type, row.source_id) == ("run", "run-1")
    assert (row.scope, row.workspace_id) == ("workspace", "ws-1")
    # 工具检索是真实召回,应累加访问统计
    assert row.access_count == 1


async def test_memory_write_uses_default_ttl(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """工具写入不暴露 ttl_days,统一采用装配时传入的部署默认有效期。"""
    tools = {
        tool.name: tool for tool in build_memory_tools(factory, default_ttl_days=5)
    }
    with bind_tool_context(ToolContext(account_id=ACCOUNT)):
        written = json.loads(
            await tools["memory_write"].handler({"content": "默认会过期的记忆"})
        )
    async with session_scope(factory) as session:
        row = await session.get(Memory, written["id"])
    assert row is not None
    assert row.expires_at is not None


async def test_memory_update_patches_fields_and_returns_id(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """memory_update 让模型就地纠正旧记忆,而不是重复写一条新的。"""
    tools = _tools(factory)
    with bind_tool_context(ToolContext(account_id=ACCOUNT, run_id="run-1")):
        written = json.loads(
            await tools["memory_write"].handler({"content": "用户偏好周报用表格"})
        )
        updated = json.loads(
            await tools["memory_update"].handler(
                {
                    "memory_id": written["id"],
                    "content": "用户偏好周报用列表",
                    "kind": "preference",
                    "pinned": True,
                }
            )
        )
    assert updated == {"updated": written["id"]}
    async with session_scope(factory) as session:
        row = await session.get(Memory, written["id"])
    assert row is not None
    assert row.content == "用户偏好周报用列表"
    assert (row.kind, row.pinned) == ("preference", True)


async def test_memory_write_is_rate_limited_per_source(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """单 run 写入限流:超过上限回错误而不是继续灌库(防投毒/刷屏)。"""
    tools = {
        tool.name: tool for tool in build_memory_tools(factory, max_writes_per_source=1)
    }
    with bind_tool_context(ToolContext(account_id=ACCOUNT, run_id="run-1")):
        first = json.loads(
            await tools["memory_write"].handler({"content": "第一条记忆"})
        )
        second = json.loads(
            await tools["memory_write"].handler({"content": "第二条记忆"})
        )
    assert set(first) == {"id"}
    assert "写入记忆过于频繁" in second["error"]


async def test_memory_write_falls_back_to_chat_source(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tools = _tools(factory)
    with bind_tool_context(ToolContext(account_id=ACCOUNT, conversation_id="conv-1")):
        written = json.loads(
            await tools["memory_write"].handler({"content": "对话里记住的事实"})
        )
    async with session_scope(factory) as session:
        row = await session.get(Memory, written["id"])
    assert row is not None
    assert (row.source_type, row.source_id) == ("chat", "conv-1")


async def test_handlers_require_tool_context(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tools = _tools(factory)
    calls: list[tuple[str, dict[str, Any]]] = [
        ("memory_search", {"query": "x"}),
        ("memory_write", {"content": "x"}),
        ("memory_delete", {"memory_id": "x"}),
    ]
    for name, args in calls:
        payload = json.loads(await tools[name].handler(args))
        assert "缺少工具上下文" in payload["error"]


async def test_handlers_validate_arguments(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tools = _tools(factory)
    with bind_tool_context(ToolContext(account_id=ACCOUNT)):
        cases: list[tuple[str, dict[str, Any], str]] = [
            ("memory_search", {"query": "   "}, "query 不能为空"),
            ("memory_write", {}, "content 不能为空"),
            ("memory_write", {"content": "x", "kind": "unknown"}, "未知记忆类型"),
            ("memory_write", {"content": "x", "scope": "team"}, "未知记忆作用域"),
            (
                "memory_write",
                {"content": "x", "scope": "workspace"},
                "需要工作区上下文",
            ),
            ("memory_delete", {}, "memory_id 不能为空"),
            ("memory_update", {}, "memory_id 不能为空"),
            (
                "memory_update",
                {"memory_id": "m-1", "kind": "unknown"},
                "未知记忆类型",
            ),
            (
                "memory_update",
                {"memory_id": "m-1", "injection_mode": "sometimes"},
                "未知注入模式",
            ),
            ("memory_update", {"memory_id": "m-1"}, "至少提供一个要修改的字段"),
        ]
        for name, args, needle in cases:
            payload = json.loads(await tools[name].handler(args))
            assert needle in payload["error"]


async def test_memory_delete_soft_deletes_and_reports_missing(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tools = _tools(factory)
    with bind_tool_context(ToolContext(account_id=ACCOUNT)):
        written = json.loads(
            await tools["memory_write"].handler({"content": "要删除的记忆"})
        )
        deleted = json.loads(
            await tools["memory_delete"].handler({"memory_id": written["id"]})
        )
        assert deleted == {"deleted": written["id"]}
        missing = json.loads(
            await tools["memory_delete"].handler({"memory_id": written["id"]})
        )
    assert "记忆不存在" in missing["error"]
    async with session_scope(factory) as session:
        row = await session.get(Memory, written["id"])
    assert row is not None
    assert row.deleted_at is not None


async def test_tools_are_account_isolated(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tools = _tools(factory)
    with bind_tool_context(ToolContext(account_id=ACCOUNT)):
        written = json.loads(
            await tools["memory_write"].handler({"content": "账号一的记忆"})
        )
    with bind_tool_context(ToolContext(account_id=OTHER)):
        hits = json.loads(await tools["memory_search"].handler({"query": "账号一"}))
        missing = json.loads(
            await tools["memory_delete"].handler({"memory_id": written["id"]})
        )
    assert hits == {"hits": []}
    assert "记忆不存在" in missing["error"]


async def test_memory_search_without_hits_returns_empty_list(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tools = _tools(factory)
    with bind_tool_context(ToolContext(account_id=ACCOUNT)):
        payload = json.loads(
            await tools["memory_search"].handler({"query": "完全不相关的查询"})
        )
    assert payload == {"hits": []}


async def test_tool_specs_declare_risk_and_schema(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tools = _tools(factory)
    assert set(tools) == {
        "memory_search",
        "memory_write",
        "memory_update",
        "memory_delete",
    }
    assert tools["memory_search"].risk is ToolRisk.READ
    assert tools["memory_write"].risk is ToolRisk.WRITE
    assert tools["memory_update"].risk is ToolRisk.WRITE
    assert tools["memory_delete"].risk is ToolRisk.WRITE
    for tool in tools.values():
        assert tool.description
        assert tool.parameters["additionalProperties"] is False
        assert tool.parameters["required"]
