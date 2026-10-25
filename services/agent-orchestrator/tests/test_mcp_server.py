"""公开 MCP Server(M3 本地版):API Key 鉴权、JSON-RPC、资源读取。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services.mcp_server import MCPPrincipal
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import _IDP, _settings

AUTH = {"Authorization": "Bearer bm_live_test"}


class FakeVerifier:
    def __init__(self, scopes: list[str] | None = None) -> None:
        self.scopes = scopes or [
            "mcp:tools:read",
            "mcp:tools:call",
            "artifacts:read",
        ]

    async def verify(self, key: str) -> MCPPrincipal:
        return MCPPrincipal(
            tenant_id="t-1",
            api_key_id="k-1",
            scopes=frozenset(self.scopes),
        )


class FakeToolkit:
    """公开面过滤用假工具箱:read(function) + ask_question(interaction)。"""

    _KINDS = {"read": "function", "ask_question": "interaction"}

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {"name": name, "parameters": {"type": "object"}},
            }
            for name in self._KINDS
        ]

    def kind(self, name: str) -> str:
        return self._KINDS.get(name, "function")

    def risk(self, name: str) -> ToolRisk:
        return ToolRisk.READ


def _client(
    tmp_path: Path,
    scopes: list[str] | None = None,
    *,
    toolkit: Any | None = None,
) -> TestClient:
    app = create_app(
        _settings(
            tmp_path,
            jobs_enabled=False,
            mcp_enabled=False,
            mcp_server_enabled=True,
        ),
        token_verifier=_IDP.verifier(),
        api_key_verifier=FakeVerifier(scopes),
        toolkit_factory=None if toolkit is None else lambda tenant: toolkit,
    )
    return TestClient(app)


def _rpc(client: TestClient, method: str, params: dict | None = None, rpc_id: int = 1):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params or {},
        },
        headers=AUTH,
    )


def test_initialize_tools_and_whoami(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        init = _rpc(client, "initialize")
        assert init.status_code == 200
        assert init.json()["result"]["protocolVersion"]

        tools = _rpc(client, "tools/list").json()["result"]["tools"]
        names = [tool["name"] for tool in tools]
        assert "beauty.whoami" in names
        assert "beauty.list_runs" in names

        whoami = _rpc(client, "tools/call", {"name": "beauty.whoami", "arguments": {}})
        assert whoami.status_code == 200
        assert "bm_live" not in whoami.text
        assert "mcp:tools:call" in whoami.text

        resources = _rpc(client, "resources/list")
        assert resources.status_code == 200
        assert resources.json()["result"]["resources"] == []


def test_tools_list_hides_interaction_tools(tmp_path: Path) -> None:
    """公开面不暴露 ask_question:交互式工具需人工应答,只走控制台(§10.9)。"""

    with _client(tmp_path, toolkit=FakeToolkit()) as client:
        tools = _rpc(client, "tools/list").json()["result"]["tools"]
        names = [tool["name"] for tool in tools]
        assert "beauty.read" in names
        assert "beauty.ask_question" not in names


class FakeRemoteToolkit:
    """tool-service 远程工具箱形状:risk() 返回旧枚举字符串,无 requires_confirmation。"""

    _TOOLS: dict[str, tuple[str, str]] = {
        "read": ("read", "function"),
        "glob": ("read", "function"),
        "grep": ("read", "function"),
        "search_knowledge": ("read", "function"),
        "write": ("write", "function"),
        "edit": ("write", "function"),
        "ask_question": ("read", "interaction"),
    }

    def __init__(self, *, failing: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failing = failing or set()

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {"name": name, "parameters": {"type": "object"}},
            }
            for name in self._TOOLS
        ]

    def kind(self, name: str) -> str:
        return self._TOOLS.get(name, ("danger", "function"))[1]

    def risk(self, name: str) -> str:
        return self._TOOLS.get(name, ("danger", "function"))[0]

    async def execute(self, name: str, arguments: str) -> str:
        self.calls.append((name, arguments))
        if name in self.failing:
            return json.dumps({"ok": False, "error": {"code": "tool.failed"}})
        return json.dumps({"ok": True, "tool": name})


def test_remote_toolkit_mcp_surface_is_read_only(tmp_path: Path) -> None:
    """tool-service 远程工具箱:list 只出只读 function;call 写/交互一律拒绝。"""

    toolkit = FakeRemoteToolkit()
    with _client(tmp_path, toolkit=toolkit) as client:
        names = [
            tool["name"]
            for tool in _rpc(client, "tools/list").json()["result"]["tools"]
        ]
        listed = set(names)
        assert listed >= {
            "beauty.read",
            "beauty.glob",
            "beauty.grep",
            "beauty.search_knowledge",
        }
        assert "beauty.write" not in listed
        assert "beauty.edit" not in listed
        assert "beauty.ask_question" not in listed

        interaction = _rpc(
            client,
            "tools/call",
            {"name": "beauty.ask_question", "arguments": {}},
        ).json()["result"]
        assert interaction["isError"] is True
        assert "可执行" in interaction["content"][0]["text"]

        denied = _rpc(
            client,
            "tools/call",
            {"name": "beauty.write", "arguments": {"path": "a.txt"}},
        ).json()["result"]
        assert denied["isError"] is True
        assert "只读" in denied["content"][0]["text"]
        assert toolkit.calls == []

        ok = _rpc(
            client,
            "tools/call",
            {"name": "beauty.read", "arguments": {"path": "a.txt"}},
        ).json()["result"]
        assert ok["isError"] is False
        assert json.loads(ok["content"][0]["text"])["ok"] is True
        assert toolkit.calls == [("read", '{"path": "a.txt"}')]


def test_remote_toolkit_call_reports_error_envelope(tmp_path: Path) -> None:
    """execute 的失败判定用 JSON 信封(ok=false),不再只看旧 {"error" 前缀。"""

    toolkit = FakeRemoteToolkit(failing={"grep"})
    with _client(tmp_path, toolkit=toolkit) as client:
        body = _rpc(
            client,
            "tools/call",
            {"name": "beauty.grep", "arguments": {"pattern": "x"}},
        ).json()["result"]
        assert body["isError"] is True
        assert json.loads(body["content"][0]["text"])["ok"] is False


def test_auth_missing_and_scope_forbidden(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        missing = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        assert missing.status_code == 401

    with _client(tmp_path, scopes=["mcp:tools:read"]) as client:
        forbidden = _rpc(
            client, "tools/call", {"name": "beauty.whoami", "arguments": {}}
        )
        assert forbidden.status_code == 200
        assert forbidden.json()["error"]["message"].startswith("缺少 scope")


def test_sse_response_when_requested(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={**AUTH, "Accept": "text/event-stream"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.text.startswith("data: ")


class FakeIntrospector:
    async def introspect(self, token: str):
        if token != "bm_oauth_valid":
            return None
        return MCPPrincipal(
            tenant_id="t-1",
            api_key_id="oauth-client",
            scopes=frozenset({"mcp:tools:read", "mcp:tools:call", "artifacts:read"}),
        )

    async def aclose(self) -> None:
        return None


def test_oauth_token_and_protected_resource_metadata(tmp_path: Path) -> None:
    from agent_orchestrator.main import create_app

    app = create_app(
        _settings(tmp_path, jobs_enabled=False, mcp_enabled=False),
        token_verifier=_IDP.verifier(),
        api_key_verifier=FakeVerifier(),
        oauth_introspector=FakeIntrospector(),
    )
    with TestClient(app) as client:
        metadata = client.get("/.well-known/oauth-protected-resource")
        assert metadata.status_code == 200
        assert metadata.json()["resource"].endswith("/mcp")
        assert metadata.json()["authorization_servers"]

        tools = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Authorization": "Bearer bm_oauth_valid"},
        )
        assert tools.status_code == 200
        names = [tool["name"] for tool in tools.json()["result"]["tools"]]
        assert "beauty.whoami" in names

        bad = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "initialize"},
            headers={"Authorization": "Bearer bm_oauth_invalid"},
        )
        assert bad.status_code == 401
        assert "oauth-protected-resource" in bad.headers["www-authenticate"]
