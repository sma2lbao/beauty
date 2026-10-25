"""stdio MCP:schema 互斥规则、注册表 create/update/allowlist 与 test 端点。"""

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_orchestrator.schemas.mcp import MCPServerCreate
from agent_orchestrator.services.mcp_client import MCPError
from tests.conftest import TENANT
from tests.support.mcp_stdio import (
    HTTP_SERVER,
    STDIO_SERVER,
    FakePool,
    _app,
    _app_with_pool,
    _create_stdio,
)


def test_schema_http_requires_url_and_rejects_stdio_fields() -> None:
    parsed = MCPServerCreate.model_validate(HTTP_SERVER)
    assert parsed.transport == "http"
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({"name": "a"})
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({**HTTP_SERVER, "command": "uvx"})
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({**HTTP_SERVER, "env": {"A": "b"}})
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({**HTTP_SERVER, "runtime": "node"})


def test_schema_stdio_rules() -> None:
    parsed = MCPServerCreate.model_validate({**STDIO_SERVER, "runtime": "node"})
    assert parsed.transport == "stdio"
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({"name": "a", "transport": "stdio"})
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({**STDIO_SERVER, "command": "rm -rf /"})
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({**STDIO_SERVER, "url": "http://x.local/mcp"})
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({**STDIO_SERVER, "token": "t"})
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({**STDIO_SERVER, "env": {"1BAD": "v"}})
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate(
            {**STDIO_SERVER, "env": {f"K{i}": "v" for i in range(51)}}
        )
    with pytest.raises(ValidationError):
        MCPServerCreate.model_validate({**STDIO_SERVER, "args": ["x" * 501]})


def test_create_stdio_masks_env_and_stores_ciphertext(tmp_path: Path) -> None:
    with _app(tmp_path) as client:
        data = _create_stdio(client)
        assert data["transport"] == "stdio"
        assert data["url"] == ""  # url 列非空,stdio 存空串
        assert data["command"] == "uvx"
        assert data["args"] == ["mcp-server-filesystem"]
        assert data["runtime"] == "python"
        assert data["image"] is None
        assert data["has_token"] is False
        assert data["has_env"] is True
        assert data["env_keys"] == ["API_TOKEN"]
        listing = client.get("/api/v1/mcp/servers", headers=TENANT).json()
        assert "S3CRET-VALUE" not in json.dumps(listing, ensure_ascii=False)

    conn = sqlite3.connect(tmp_path / "stdio.db")
    try:
        row = conn.execute(
            "select transport, url, encrypted_env from mcp_servers"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "stdio"
    assert row[1] == ""
    assert row[2] and "S3CRET-VALUE" not in row[2]


def test_update_stdio_args_and_clear_env(tmp_path: Path) -> None:
    with _app(tmp_path) as client:
        created = _create_stdio(client)
        updated = client.patch(
            f"/api/v1/mcp/servers/{created['id']}",
            json={"args": ["mcp-server-git"]},
            headers=TENANT,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["args"] == ["mcp-server-git"]
        assert updated.json()["has_env"] is True  # 没传 env 就保留

        cleared = client.patch(
            f"/api/v1/mcp/servers/{created['id']}",
            json={"env": {}},
            headers=TENANT,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["has_env"] is False
        assert cleared.json()["env_keys"] == []


def test_update_stdio_to_http_requires_clearing_command_fields(tmp_path: Path) -> None:
    with _app(tmp_path) as client:
        created = _create_stdio(client)
        response = client.patch(
            f"/api/v1/mcp/servers/{created['id']}",
            json={"transport": "http", "url": "http://127.0.0.1:9999/mcp"},
            headers=TENANT,
        )
        # 合并后仍有 command/args/env → 明确拒绝(400 mcp.invalid_config)
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "mcp.invalid_config"


def test_create_stdio_command_must_be_allowlisted(tmp_path: Path) -> None:
    with _app(tmp_path, "deny.db") as client:
        response = client.post(
            "/api/v1/mcp/servers",
            json={**STDIO_SERVER, "name": "evil", "command": "curl"},
            headers=TENANT,
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "mcp.command_denied"


def test_allowlist_comes_from_settings(tmp_path: Path) -> None:
    with _app(tmp_path, "allow.db", mcp_stdio_command_allowlist=["uvx"]) as client:
        ok = client.post(
            "/api/v1/mcp/servers",
            json={**STDIO_SERVER, "name": "ok"},
            headers=TENANT,
        )
        assert ok.status_code == 201, ok.text
        denied = client.post(
            "/api/v1/mcp/servers",
            json={**STDIO_SERVER, "name": "node-one", "command": "node"},
            headers=TENANT,
        )
        assert denied.status_code == 409, denied.text


def test_test_server_stdio_caches_tools_via_pool(tmp_path: Path) -> None:
    pool = FakePool()
    with _app_with_pool(tmp_path, pool) as client:
        created = _create_stdio(client)
        tested = client.post(
            f"/api/v1/mcp/servers/{created['id']}/test", headers=TENANT
        )
        assert tested.status_code == 200, tested.text
        assert tested.json()["ok"] is True
        assert tested.json()["tools"][0]["name"] == "echo"

        listing = client.get("/api/v1/mcp/servers", headers=TENANT).json()
        assert listing[0]["tools"][0]["name"] == "echo"
        assert listing[0]["last_status"] == "ok"

    tenant_id, key, request = pool.calls[0]
    assert tenant_id.startswith("t-")
    assert key.startswith(f"test:{created['id']}:")
    assert request["action"] == "tools/list"
    assert request["command"] == "uvx"
    assert request["env"] == {"API_TOKEN": "S3CRET-VALUE"}
    assert pool.released[0][1] == key


def test_test_server_stdio_without_pool_reports_sandbox_disabled(
    tmp_path: Path,
) -> None:
    with _app(tmp_path, "nopool.db") as client:
        created = _create_stdio(client)
        tested = client.post(
            f"/api/v1/mcp/servers/{created['id']}/test", headers=TENANT
        ).json()
        assert tested["ok"] is False
        assert "需要启用沙箱" in tested["detail"]
        listing = client.get("/api/v1/mcp/servers", headers=TENANT).json()
        assert listing[0]["last_status"] == "error"


def test_test_server_stdio_pool_error_is_readable(tmp_path: Path) -> None:
    pool = FakePool(error=MCPError("沙箱炸了"))
    with _app_with_pool(tmp_path, pool) as client:
        created = _create_stdio(client)
        tested = client.post(
            f"/api/v1/mcp/servers/{created['id']}/test", headers=TENANT
        ).json()
        assert tested["ok"] is False
        assert "沙箱炸了" in tested["detail"]
        assert pool.released  # 临时沙箱必须释放
