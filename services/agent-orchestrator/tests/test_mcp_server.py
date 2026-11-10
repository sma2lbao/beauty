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
LEGACY = "2025-03-26"
STATELESS = "2026-07-28"


class FakeVerifier:
    def __init__(self, scopes: list[str] | None = None) -> None:
        self.scopes = scopes or [
            "mcp:tools:read",
            "mcp:tools:call",
            "artifacts:read",
        ]

    async def verify(self, key: str) -> MCPPrincipal:
        return MCPPrincipal(
            account_id="t-1",
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
        toolkit_factory=None if toolkit is None else lambda account: toolkit,
    )
    return TestClient(app)


def _rpc(
    client: TestClient,
    method: str,
    params: dict | None = None,
    rpc_id: int = 1,
    *,
    version: str | None = None,
    accept: str | None = None,
):
    headers = dict(AUTH)
    if version is not None:
        headers["MCP-Protocol-Version"] = version
    if accept is not None:
        headers["Accept"] = accept
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params or {},
        },
        headers=headers,
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
            account_id="t-1",
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


# ---------------------------------------------------------------------------
# 双栈(P1-H,见 docs/design/mcp-dual-stack.md §6):版本头分流
# ---------------------------------------------------------------------------


def test_stateless_discover_and_sessionless_requests(tmp_path: Path) -> None:
    """2026-07-28:server/discover 可用、免 initialize、不签会话、响应回告版本。"""
    with _client(tmp_path) as client:
        discover = _rpc(client, "server/discover", version=STATELESS)
        assert discover.status_code == 200
        result = discover.json()["result"]
        assert result["protocolVersions"] == [STATELESS, LEGACY]
        assert result["protocolVersion"] == STATELESS
        assert result["serverInfo"]["name"] == "beauty-moon"
        assert set(result["capabilities"]) >= {"tools", "resources"}
        assert result["instructions"]
        assert discover.headers["MCP-Protocol-Version"] == STATELESS
        assert "mcp-session-id" not in discover.headers

        init = _rpc(client, "initialize", version=STATELESS)
        assert init.status_code == 200
        assert init.json()["result"]["protocolVersion"] == STATELESS
        assert "mcp-session-id" not in init.headers

        # 不握手也能直接列工具,且响应仍带版本头
        tools = _rpc(client, "tools/list", version=STATELESS)
        assert tools.status_code == 200
        names = [tool["name"] for tool in tools.json()["result"]["tools"]]
        assert "beauty.whoami" in names
        assert tools.headers["MCP-Protocol-Version"] == STATELESS
        assert "mcp-session-id" not in tools.headers

    # 分流不改变授权面:scope 仍逐方法校验
    with _client(tmp_path, scopes=["artifacts:read"]) as client:
        forbidden = _rpc(client, "tools/list", version=STATELESS)
        assert forbidden.status_code == 200
        assert forbidden.json()["error"]["message"].startswith("缺少 scope")


def test_legacy_stack_versions_and_unknown_discover(tmp_path: Path) -> None:
    """旧栈(无头或 2025-03-26):initialize 回旧版本;server/discover 不支持。"""
    with _client(tmp_path) as client:
        bare = _rpc(client, "initialize")
        assert bare.status_code == 200
        assert bare.json()["result"]["protocolVersion"] == LEGACY
        assert "MCP-Protocol-Version" not in bare.headers

        explicit = _rpc(client, "initialize", version=LEGACY)
        assert explicit.json()["result"]["protocolVersion"] == LEGACY
        assert "MCP-Protocol-Version" not in explicit.headers

        discover = _rpc(client, "server/discover", version=LEGACY)
        assert discover.status_code == 200
        assert discover.json()["error"]["code"] == -32601


def test_unsupported_protocol_version_is_rejected_before_auth(tmp_path: Path) -> None:
    """未知版本头:HTTP 400 + -32600 + supported_versions(先于鉴权判定)。"""
    with _client(tmp_path) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"MCP-Protocol-Version": "2024-01-01"},
        )
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == -32600
        assert error["data"]["supported_versions"] == [STATELESS, LEGACY]

        # 带合法凭证也是同样的确定性协议错误
        unauthorized = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "initialize"},
            headers={**AUTH, "MCP-Protocol-Version": "2024-01-01"},
        )
        assert unauthorized.status_code == 400


def test_stateless_method_matrix_and_http_verbs(tmp_path: Path) -> None:
    """无状态栈:未知方法 -32601;GET 405;DELETE 204(旧栈仍 405)。"""
    with _client(tmp_path) as client:
        subscription = _rpc(client, "subscriptions/listen", version=STATELESS)
        assert subscription.status_code == 200
        assert subscription.json()["error"]["code"] == -32601
        assert "不支持" in subscription.json()["error"]["message"]

        get_response = client.get(
            "/mcp", headers={**AUTH, "MCP-Protocol-Version": STATELESS}
        )
        assert get_response.status_code == 405

        deleted = client.delete(
            "/mcp", headers={**AUTH, "MCP-Protocol-Version": STATELESS}
        )
        assert deleted.status_code == 204

        legacy_delete = client.delete("/mcp", headers=AUTH)
        assert legacy_delete.status_code == 405

        anonymous_delete = client.delete(
            "/mcp", headers={"MCP-Protocol-Version": STATELESS}
        )
        assert anonymous_delete.status_code == 401
