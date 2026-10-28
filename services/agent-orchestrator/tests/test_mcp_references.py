"""对话/任务的 MCP 显式引用(§6.8):只装配被引用 server 的工具。

口径:

- 引用是"收窄"而不是"追加":选了 server 就只装配这些 server 的 MCP 工具;
- 不引用 = 现状全量(向后兼容);
- 引用在落库前校验(未知 404、停用 409、能力未启用 409),不留半残引用;
- 引用随消息/run 落库,并进系统提示词(模型据此对号入座)。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from common.errors import AppError
from database.base import Base
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.services import mcp as mcp_service
from tests.conftest import TENANT, FakeLLM, verifier
from tests.conftest import _settings as base_settings

_ECHO_TOOL = {"name": "echo", "description": "回显", "inputSchema": {}}
_PING_TOOL = {"name": "ping", "description": "探活", "inputSchema": {}}
VERIFY_PASS = '{"summary": "ok", "verdicts": {"日程已整理": true}}'


def _settings(tmp_path: Path, **overrides: Any):
    defaults: dict[str, Any] = {
        "llm_enabled": True,
        "mcp_enabled": True,
        "mcp_secret": "test-mcp-secret",
    }
    defaults.update(overrides)
    return base_settings(tmp_path, "mcp-refs.db", **defaults)


def _seed_servers(settings, rows: list[dict[str, Any]]) -> list[str]:
    """在 app 启动前直接落库(工具缓存只能由 /test 刷新,测试里直接给)。"""

    async def run() -> list[str]:
        factory = create_session_factory(settings.database_url)
        try:
            async with factory.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            ids: list[str] = []
            async with session_scope(factory) as session:
                for row in rows:
                    server = MCPServer(
                        tenant_id="t-1",
                        name=row["name"],
                        url=row.get("url", "http://mcp.local/mcp"),
                        transport="http",
                        enabled=row.get("enabled", True),
                        auto_approve=row.get("auto_approve", True),
                        timeout_seconds=5,
                        args=[],
                        tools_allowlist=[],
                        tools_cache=row.get("tools_cache", []),
                    )
                    session.add(server)
                    await session.flush()
                    ids.append(server.id)
            return ids
        finally:
            await factory.engine.dispose()

    return asyncio.run(run())


def _client(settings, fake: FakeLLM | None = None) -> TestClient:
    return TestClient(
        create_app(
            settings,
            llm_client=fake,
            token_verifier=verifier(),
        )
    )


def _create_conversation(client: TestClient) -> str:
    resp = client.post("/api/v1/conversations", json={}, headers=TENANT)
    assert resp.status_code == 201
    return resp.json()["id"]


def _stream(
    client: TestClient,
    conversation_id: str,
    payload: dict[str, Any],
    mode: str = "chat",
):
    body = {"mode": mode, **payload}
    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json=body,
        headers=TENANT,
    )
    events = []
    for frame in resp.text.split("\n\n"):
        name = data = None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if name and data:
            events.append((name, json.loads(data)))
    return resp, events


def _tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    assert tools is not None
    return sorted(tool["function"]["name"] for tool in tools)


def _system_prompt(fake: FakeLLM, call: int = 0) -> str:
    return str(fake.calls[call][0]["content"])


# ---- 单元:解析引用与提示词 ----


def test_resolve_selected_dedupes_keeps_order_and_rejects_bad_refs(tmp_path) -> None:
    settings = _settings(tmp_path)
    alpha, beta, off = _seed_servers(
        settings,
        [
            {"name": "Alpha"},
            {"name": "Beta"},
            {"name": "已停用", "enabled": False},
        ],
    )

    async def scenario() -> None:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                selected = await mcp_service.resolve_selected(
                    session, "t-1", [beta, alpha, beta]
                )
                assert [server.id for server in selected] == [beta, alpha]

                with pytest.raises(AppError) as unknown:
                    await mcp_service.resolve_selected(session, "t-1", [alpha, "gone"])
                assert unknown.value.code == "mcp.server_not_found"
                assert unknown.value.details["mcp_server_ids"] == ["gone"]

                with pytest.raises(AppError) as disabled:
                    await mcp_service.resolve_selected(session, "t-1", [off])
                assert disabled.value.code == "mcp.server_disabled"
                assert "已停用" in disabled.value.message
        finally:
            await factory.engine.dispose()

    asyncio.run(scenario())


def test_reference_note_names_servers_and_matches_tool_namespace() -> None:
    def server(name: str) -> MCPServer:
        return MCPServer(tenant_id="t-1", name=name, url="http://mcp.local/mcp")

    # 空引用 = 不加任何提示词(不选就是现状)
    assert mcp_service.reference_note([]) == ""

    note = mcp_service.reference_note([server("Demo Server"), server("Linear")])
    assert "明确引用了 MCP server" in note
    assert "Demo Server" in note and "Linear" in note
    # 前缀与 tool_specs 的命名一致,模型才能对号入座
    assert "mcp__demo_server__" in note
    assert "未列出的 MCP server 本次不可用" in note


def test_tool_specs_only_builds_selected_servers(monkeypatch) -> None:
    first = MCPServer(
        id="srv-alpha",
        tenant_id="t-1",
        name="Alpha",
        url="http://mcp.local/mcp",
        enabled=True,
        auto_approve=True,
        tools_cache=[_ECHO_TOOL],
    )
    second = MCPServer(
        id="srv-beta",
        tenant_id="t-1",
        name="Beta",
        url="http://mcp.local/mcp",
        enabled=True,
        auto_approve=True,
        tools_cache=[_PING_TOOL],
    )

    class FakeRepo:
        def __init__(self, session) -> None:
            pass

        async def list_scoped(self, *args, **kwargs):
            return [first, second]

    monkeypatch.setattr(mcp_service, "MCPServerRepository", FakeRepo)

    async def scenario() -> None:
        all_specs = await mcp_service.tool_specs(None, "t", secret="x")  # type: ignore[arg-type]
        assert sorted(spec.name for spec in all_specs) == [
            "mcp__alpha__echo",
            "mcp__beta__ping",
        ]

        only_beta = await mcp_service.tool_specs(  # type: ignore[arg-type]
            None, "t", secret="x", server_ids=["srv-beta"]
        )
        assert [spec.name for spec in only_beta] == ["mcp__beta__ping"]

    asyncio.run(scenario())


# ---- 接口:消息 / run 的引用校验与落库 ----


def test_workshop_message_validates_and_persists_mcp_references(tmp_path) -> None:
    settings = _settings(tmp_path)
    alpha, off = _seed_servers(
        settings, [{"name": "Alpha"}, {"name": "已停用", "enabled": False}]
    )

    fake = FakeLLM(["好的"])
    with _client(settings, fake) as client:
        conversation = _create_conversation(client)

        unknown = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={"content": "hi", "mode": "chat", "mcp_server_ids": ["gone"]},
            headers=TENANT,
        )
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "mcp.server_not_found"

        disabled = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={"content": "hi", "mode": "chat", "mcp_server_ids": [off]},
            headers=TENANT,
        )
        assert disabled.status_code == 409
        assert disabled.json()["code"] == "mcp.server_disabled"

        # 被拒绝的消息不落库:引用校验在落库前完成
        messages = client.get(
            f"/api/v1/conversations/{conversation}/messages", headers=TENANT
        ).json()
        assert messages == []

        accepted = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={
                "content": "用 alpha 回显",
                "mode": "chat",
                # 重复引用去重后落库
                "mcp_server_ids": [alpha, alpha],
            },
            headers=TENANT,
        )
        assert accepted.status_code == 201
        assert accepted.json()["message"]["mcp_server_ids"] == [alpha]

        messages = client.get(
            f"/api/v1/conversations/{conversation}/messages", headers=TENANT
        ).json()
        assert messages[0]["mcp_server_ids"] == [alpha]


def test_message_reference_requires_mcp_enabled(tmp_path) -> None:
    settings = _settings(tmp_path, mcp_enabled=False)
    (alpha,) = _seed_servers(settings, [{"name": "Alpha"}])

    fake = FakeLLM(["好的"])
    with _client(settings, fake) as client:
        conversation = _create_conversation(client)
        resp = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={"content": "hi", "mode": "chat", "mcp_server_ids": [alpha]},
            headers=TENANT,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "mcp.disabled"


def test_chat_stream_uses_only_referenced_mcp_tools(tmp_path) -> None:
    """端到端:引用 Alpha → 工具表只有 Alpha 的工具,提示词带引用片段。"""
    settings = _settings(tmp_path)
    alpha, _beta = _seed_servers(
        settings,
        [
            {"name": "Alpha", "tools_cache": [_ECHO_TOOL]},
            {"name": "Beta", "tools_cache": [_PING_TOOL]},
        ],
    )

    fake = FakeLLM(["好的", "好的"])
    with _client(settings, fake) as client:
        conversation = _create_conversation(client)

        resp, events = _stream(
            client,
            conversation,
            {"content": "用 alpha 回显", "mcp_server_ids": [alpha]},
        )
        assert resp.status_code == 200
        assert [name for name, _ in events if name == "error"] == []

        # 被引用的 server 才装配(未列出的 Beta 本次不可用)
        assert _tool_names(fake.tools_seen[0]) == ["mcp__alpha__echo"]
        prompt = _system_prompt(fake)
        assert "明确引用了 MCP server:Alpha" in prompt
        assert "mcp__alpha__" in prompt

        # 不引用 = 现状全量
        _stream(client, conversation, {"content": "随便聊聊"})
        assert _tool_names(fake.tools_seen[1]) == [
            "mcp__alpha__echo",
            "mcp__beta__ping",
        ]


def test_run_api_validates_and_snapshots_mcp_references(tmp_path) -> None:
    settings = _settings(tmp_path)
    alpha, off = _seed_servers(
        settings, [{"name": "Alpha"}, {"name": "已停用", "enabled": False}]
    )

    with _client(settings) as client:
        created = client.post(
            "/api/v1/runs",
            json={
                "title": "带引用的任务",
                "goal": "用 alpha 回显",
                "mcp_server_ids": [alpha],
            },
            headers=TENANT,
        )
        assert created.status_code == 201
        body = created.json()
        assert body["mcp_server_ids"] == [alpha]

        # 快照可查(建 run 时固化,后续改引用不影响已建 run)
        fetched = client.get(f"/api/v1/runs/{body['id']}", headers=TENANT).json()
        assert fetched["mcp_server_ids"] == [alpha]

        unknown = client.post(
            "/api/v1/runs",
            json={"title": "x", "goal": "x", "mcp_server_ids": ["gone"]},
            headers=TENANT,
        )
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "mcp.server_not_found"

        disabled = client.post(
            "/api/v1/runs",
            json={"title": "x", "goal": "x", "mcp_server_ids": [off]},
            headers=TENANT,
        )
        assert disabled.status_code == 409
        assert disabled.json()["code"] == "mcp.server_disabled"


def test_run_execution_uses_snapshotted_mcp_references(tmp_path) -> None:
    """run 执行期按快照装配:引用 Alpha 的 run 只看得到 Alpha 的工具。"""
    settings = _settings(tmp_path)
    alpha, _beta = _seed_servers(
        settings,
        [
            {"name": "Alpha", "tools_cache": [_ECHO_TOOL]},
            {"name": "Beta", "tools_cache": [_PING_TOOL]},
        ],
    )

    fake = FakeLLM(["# 计划", "执行完成", VERIFY_PASS, "# 交付物"])
    with _client(settings, fake) as client:
        created = client.post(
            "/api/v1/runs",
            json={"title": "周报", "goal": "整理本周日程", "mcp_server_ids": [alpha]},
            headers=TENANT,
        )
        run_id = created.json()["id"]
        planned = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        assert planned.status_code == 200
        done = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert done["status"] == "completed"

        # 第 0 次是 plan,第 1 次是 execute:工具表来自 run 快照里的引用
        assert _tool_names(fake.tools_seen[1]) == [
            "mcp__alpha__echo",
            "spawn_subagent",
            "todo_write",
        ]


def test_run_reference_requires_mcp_enabled(tmp_path) -> None:
    settings = _settings(tmp_path, mcp_enabled=False)
    (alpha,) = _seed_servers(settings, [{"name": "Alpha"}])

    with _client(settings) as client:
        resp = client.post(
            "/api/v1/runs",
            json={"title": "x", "goal": "x", "mcp_server_ids": [alpha]},
            headers=TENANT,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "mcp.disabled"
