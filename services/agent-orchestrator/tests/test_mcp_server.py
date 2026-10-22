"""公开 MCP Server(M3 本地版):API Key 鉴权、JSON-RPC、资源读取。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services.mcp_server import MCPPrincipal
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


def _client(tmp_path: Path, scopes: list[str] | None = None) -> TestClient:
    app = create_app(
        _settings(
            tmp_path,
            jobs_enabled=False,
            mcp_enabled=False,
            mcp_server_enabled=True,
        ),
        token_verifier=_IDP.verifier(),
        api_key_verifier=FakeVerifier(scopes),
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
