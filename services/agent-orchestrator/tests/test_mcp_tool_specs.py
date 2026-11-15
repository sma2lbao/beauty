"""MCP 工具规格:命名空间、白名单、免确认与调用期 401 的结构化错误。"""

from __future__ import annotations

import json

from agent_orchestrator.services.mcp_client import MCPAuthRequired


def test_tool_specs_namespace_and_handler(monkeypatch) -> None:
    import asyncio

    from agent_orchestrator.models.mcp import MCPServer
    from agent_orchestrator.services import mcp as mcp_service
    from agent_orchestrator.services import mcp_runtime

    server = MCPServer(
        account_id="t",
        name="Demo Server",
        url="http://mcp.local/mcp",
        encrypted_token=None,
        token_hint=None,
        enabled=True,
        tools_allowlist=["echo"],
        tools_cache=[
            {"name": "echo", "description": "回显", "inputSchema": {}},
            {"name": "blocked", "description": "不在白名单", "inputSchema": {}},
        ],
        timeout_seconds=2,
    )

    class FakeRepo:
        def __init__(self, session) -> None:
            pass

        async def list_scoped(self, *args, **kwargs):
            return [server]

    class FakeClient:
        async def initialize(self) -> dict:
            return {}

        async def call_tool(self, name: str, arguments: dict) -> str:
            return f"{name}:{arguments.get('text')}"

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(mcp_service, "MCPServerRepository", FakeRepo)
    monkeypatch.setattr(
        mcp_runtime, "build_client", lambda *args, **kwargs: FakeClient()
    )

    async def scenario() -> None:
        specs = await mcp_service.tool_specs(None, "t", secret="x")  # type: ignore[arg-type]
        assert [spec.name for spec in specs] == ["mcp__demo_server__echo"]
        assert specs[0].requires_confirmation is True
        assert await specs[0].handler({"text": "hi"}) == "echo:hi"

        # auto_approve=True 的 server:工具免确认(对话路径才可能执行)
        server.auto_approve = True
        trusted = await mcp_service.tool_specs(None, "t", secret="x")  # type: ignore[arg-type]
        assert [spec.requires_confirmation for spec in trusted] == [False]

    asyncio.run(scenario())


def test_tool_handler_401_returns_structured_error_and_marks(monkeypatch) -> None:
    """调用期 401:回喂结构化错误(不执行),并尽力上报落 last_status。"""
    import asyncio

    from agent_orchestrator.models.mcp import MCPServer
    from agent_orchestrator.services import mcp as mcp_service
    from agent_orchestrator.services import mcp_runtime

    server = MCPServer(
        id="srv-1",
        account_id="t",
        name="Demo Server",
        url="http://mcp.local/mcp",
        encrypted_token=None,
        token_hint=None,
        enabled=True,
        tools_allowlist=[],
        tools_cache=[{"name": "echo", "description": "", "inputSchema": {}}],
        timeout_seconds=2,
    )

    class FakeRepo:
        def __init__(self, session) -> None:
            pass

        async def list_scoped(self, *args, **kwargs):
            return [server]

    class UnauthorizedClient:
        async def initialize(self) -> dict:
            return {}

        async def call_tool(self, name: str, arguments: dict) -> str:
            raise MCPAuthRequired("MCP 服务端要求授权(HTTP 401)")

        async def aclose(self) -> None:
            return None

    marks: list[tuple[str, str]] = []

    async def mark(server_id: str, detail: str) -> None:
        marks.append((server_id, detail))
        # 标记是尽力而为:即使写库失败也不能影响工具返回
        raise RuntimeError("database is locked")

    monkeypatch.setattr(mcp_service, "MCPServerRepository", FakeRepo)
    monkeypatch.setattr(
        mcp_runtime, "build_client", lambda *args, **kwargs: UnauthorizedClient()
    )

    async def scenario() -> None:
        specs = await mcp_service.tool_specs(
            None,  # type: ignore[arg-type]
            "t",
            secret="x",
            on_auth_required=mark,
        )
        payload = json.loads(await specs[0].handler({}))
        assert payload["code"] == "mcp.auth_required"
        assert "401" in payload["detail"]
        assert payload["hint"]
        assert marks == [("srv-1", "MCP 服务端要求授权(HTTP 401)")]

    asyncio.run(scenario())
