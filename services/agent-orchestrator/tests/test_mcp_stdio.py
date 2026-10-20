"""stdio 型 MCP(P2):schema、注册表、沙箱池、probe 协议(全部零网络)。

覆盖:
- schema 的 http/stdio 互斥规则与 env/args 上限;
- create/update 落库口径(url 空、env 密文、has_env/env_keys、allowlist);
- FakePool:test 端点刷新 tools_cache、tool_specs 按对话上下文选作用域 key;
- StdioSandboxPool:FakeSandbox 记录 create/touch/exec/destroy、marker 解析;
- PROBE_PY / PROBE_JS 本地联调:tmp_path 里起 dummy stdio MCP server。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from common.errors import UpstreamError
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_orchestrator.main import create_app
from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.schemas.mcp import MCPServerCreate
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services.mcp_client import MCPError
from agent_orchestrator.services.mcp_stdio import (
    MARKER,
    PROBE_JS,
    PROBE_PY,
    StdioSandboxPool,
)
from agent_orchestrator.services.sandbox import SandboxExecResult, SandboxInfo
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from tests.conftest import TENANT, _settings, verifier

HTTP_SERVER = {
    "name": "demo",
    "url": "http://127.0.0.1:9999/mcp",
    "timeout_seconds": 5,
}

STDIO_SERVER = {
    "name": "files",
    "transport": "stdio",
    "command": "uvx",
    "args": ["mcp-server-filesystem"],
    "env": {"API_TOKEN": "S3CRET-VALUE"},
    "runtime": "python",
    "timeout_seconds": 5,
}


def _app(tmp_path: Path, name: str = "stdio.db", **overrides: Any) -> TestClient:
    settings = _settings(tmp_path, name, **overrides)
    app = create_app(settings, token_verifier=verifier())
    return TestClient(app)


def _create_stdio(client: TestClient, **overrides: Any) -> dict:
    body = {**STDIO_SERVER, **overrides}
    response = client.post("/api/v1/mcp/servers", json=body, headers=TENANT)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 注册表:create / update / allowlist
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# FakePool:test 端点与工具注入
# ---------------------------------------------------------------------------


class FakePool:
    """stdio 沙箱池替身:记录调用,按 action 返回脚本化结果。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.released: list[tuple[str, str]] = []
        self.forgotten: list[str] = []

    async def run(
        self, tenant_id: str, key: str, server: MCPServer, request: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((tenant_id, key, request))
        if self.error is not None:
            raise self.error
        if request.get("action") == "tools/call":
            arguments = request.get("arguments") or {}
            return {
                "content": [{"type": "text", "text": f"echo:{arguments.get('text')}"}]
            }
        return {"tools": [{"name": "echo", "description": "回显", "inputSchema": {}}]}

    async def release(self, tenant_id: str, key: str) -> None:
        self.released.append((tenant_id, key))

    def forget(self, key: str) -> None:
        self.forgotten.append(key)


def _app_with_pool(tmp_path: Path, pool: FakePool) -> TestClient:
    settings = _settings(tmp_path, "pool.db")
    app = create_app(settings, token_verifier=verifier(), mcp_stdio_pool=pool)
    return TestClient(app)


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


def _stdio_model() -> MCPServer:
    server = MCPServer(
        tenant_id="t-1",
        name="fs mcp",
        url="",
        transport="stdio",
        command="uvx",
        args=["mcp-server-filesystem"],
        runtime="python",
        encrypted_env=None,
        enabled=True,
        tools_allowlist=[],
        tools_cache=[{"name": "read_file", "description": "读文件", "inputSchema": {}}],
        timeout_seconds=5,
    )
    server.id = "srv-1"
    return server


def _patch_repo(monkeypatch, server: MCPServer) -> None:
    class FakeRepo:
        def __init__(self, session) -> None:
            pass

        async def list_scoped(self, *args, **kwargs):
            return [server]

    monkeypatch.setattr(mcp_service, "MCPServerRepository", FakeRepo)


async def test_tool_specs_stdio_handler_uses_conversation_scope(
    monkeypatch,
) -> None:
    server = _stdio_model()
    _patch_repo(monkeypatch, server)
    pool = FakePool()
    specs = await mcp_service.tool_specs(None, "t-1", secret="x", pool=pool)  # type: ignore[arg-type]
    assert [spec.name for spec in specs] == ["mcp__fs_mcp__read_file"]
    assert specs[0].requires_confirmation is True

    with bind_tool_context(ToolContext(tenant_id="t-1", conversation_id="conv-1")):
        output = await specs[0].handler({"text": "hi"})
    assert output == "echo:hi"
    assert pool.calls[0][1] == "conv-1:srv-1"
    assert pool.calls[0][2]["action"] == "tools/call"
    assert pool.calls[0][2]["tool"] == "read_file"

    # 没有对话时退化为 run 作用域
    with bind_tool_context(ToolContext(tenant_id="t-1", run_id="run-9")):
        await specs[0].handler({"text": "hi"})
    assert pool.calls[1][1] == "run:run-9:srv-1"


async def test_tool_specs_stdio_handler_feeds_back_errors(monkeypatch) -> None:
    server = _stdio_model()
    _patch_repo(monkeypatch, server)
    pool = FakePool(error=MCPError("沙箱挂了"))
    specs = await mcp_service.tool_specs(None, "t-1", secret="x", pool=pool)  # type: ignore[arg-type]
    output = await specs[0].handler({})
    assert json.loads(output)["error"].endswith("沙箱挂了")


async def test_tool_specs_skips_stdio_without_pool(monkeypatch) -> None:
    server = _stdio_model()
    _patch_repo(monkeypatch, server)
    specs = await mcp_service.tool_specs(None, "t-1", secret="x", pool=None)  # type: ignore[arg-type]
    assert specs == []


# ---------------------------------------------------------------------------
# StdioSandboxPool(用 FakeSandbox 记录生命周期)
# ---------------------------------------------------------------------------


class FakeSandbox:
    """Sandbox 协议替身:记录 create/touch/exec/destroy,exec 返回脚本化输出。"""

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        touch_error: Exception | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.touch_error = touch_error
        self.created: list[dict[str, Any]] = []
        self.touched: list[str] = []
        self.execs: list[dict[str, Any]] = []
        self.destroyed: list[str] = []
        self.files: dict[str, bytes] = {}

    async def create(self, tenant_id: str, **kwargs: Any) -> SandboxInfo:
        self.created.append({"tenant_id": tenant_id, **kwargs})
        index = len(self.created)
        return SandboxInfo(
            id=f"sb-{index}",
            name=f"bm-{index}",
            status="running",
            kind=str(kwargs.get("kind") or "generic"),
            image=kwargs.get("image"),
            network=str(kwargs.get("network") or "deny"),
        )

    async def touch(self, tenant_id: str, sandbox_id: str) -> SandboxInfo:
        self.touched.append(sandbox_id)
        if self.touch_error is not None:
            raise self.touch_error
        return SandboxInfo(id=sandbox_id, name="bm", status="running")

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv,
        *,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        self.execs.append(
            {
                "sandbox_id": sandbox_id,
                "argv": list(argv),
                "cwd": cwd,
                "env": env,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        return SandboxExecResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_ms=1,
            truncated=False,
        )

    async def write_file(
        self, tenant_id: str, sandbox_id: str, path: str, data: bytes
    ) -> None:
        self.files[f"{sandbox_id}:{path}"] = data

    async def destroy(self, tenant_id: str, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)

    async def aclose(self) -> None:
        return None


def _ok_stdout(result: Any) -> str:
    return MARKER + json.dumps({"ok": True, "result": result}, ensure_ascii=False)


async def test_pool_run_reuses_sandbox_and_writes_probe_once() -> None:
    sandbox = FakeSandbox(stdout=_ok_stdout({"tools": [{"name": "echo"}]}))
    pool = StdioSandboxPool(
        sandbox, ttl_seconds=60, network="public", timeout_seconds=60
    )
    server = _stdio_model()
    request = {"command": "uvx", "args": [], "env": {}, "action": "tools/list"}

    first = await pool.run("t-1", "conv-1:srv-1", server, request)
    second = await pool.run("t-1", "conv-1:srv-1", server, request)

    assert first == second == {"tools": [{"name": "echo"}]}
    assert len(sandbox.created) == 1
    assert sandbox.created[0]["kind"] == "mcp"
    assert sandbox.created[0]["image"] == "python"
    assert sandbox.created[0]["network"] == "public"
    assert sandbox.created[0]["ttl_seconds"] == 60
    assert sandbox.touched == ["sb-1"]
    assert len(sandbox.execs) == 2
    assert sandbox.execs[0]["argv"] == ["python3", "/workspace/.mcp/probe.py"]
    assert sandbox.execs[0]["timeout_seconds"] == 60  # max(60, 5+30)
    assert json.loads(sandbox.execs[0]["stdin"]) == request
    assert len(sandbox.files) == 1  # probe 只写一次
    assert (sandbox.files["sb-1:/workspace/.mcp/probe.py"]).decode() == PROBE_PY
    assert pool.cached_ids == {"conv-1:srv-1": "sb-1"}

    await pool.release("t-1", "conv-1:srv-1")
    assert sandbox.destroyed == ["sb-1"]
    assert pool.cached_ids == {}


async def test_pool_node_runtime_uses_probe_js() -> None:
    sandbox = FakeSandbox(stdout=_ok_stdout([]))
    pool = StdioSandboxPool(sandbox)
    server = _stdio_model()
    server.runtime = "node"
    await pool.run(
        "t-1", "k", server, {"command": "npx", "args": [], "action": "tools/list"}
    )
    assert sandbox.created[0]["image"] == "node"
    assert sandbox.execs[0]["argv"] == ["node", "/workspace/.mcp/probe.js"]
    assert sandbox.files["sb-1:/workspace/.mcp/probe.js"].decode() == PROBE_JS


async def test_pool_rebuilds_when_touch_fails() -> None:
    sandbox = FakeSandbox(
        stdout=_ok_stdout({}),
        touch_error=UpstreamError("sandbox.upstream_error", "sandbox gone"),
    )
    pool = StdioSandboxPool(sandbox)
    server = _stdio_model()
    request = {"command": "uvx", "args": [], "action": "tools/list"}
    await pool.run("t-1", "k", server, request)
    await pool.run("t-1", "k", server, request)
    assert len(sandbox.created) == 2
    assert pool.cached_ids["k"] == "sb-2"


async def test_pool_missing_marker_raises_with_stderr_tail() -> None:
    sandbox = FakeSandbox(stdout="hello\n", stderr="boom-stderr")
    pool = StdioSandboxPool(sandbox)
    with pytest.raises(MCPError) as excinfo:
        await pool.run(
            "t-1",
            "k",
            _stdio_model(),
            {"command": "uvx", "args": [], "action": "tools/list"},
        )
    assert "marker" in str(excinfo.value)
    assert "boom-stderr" in str(excinfo.value)


async def test_pool_probe_error_payload_raises() -> None:
    sandbox = FakeSandbox(
        stdout=MARKER + json.dumps({"ok": False, "error": "进程启动失败"})
    )
    pool = StdioSandboxPool(sandbox)
    with pytest.raises(MCPError, match="进程启动失败"):
        await pool.run(
            "t-1",
            "k",
            _stdio_model(),
            {"command": "uvx", "args": [], "action": "tools/list"},
        )


async def test_pool_rejects_unknown_runtime() -> None:
    sandbox = FakeSandbox(stdout=_ok_stdout({}))
    pool = StdioSandboxPool(sandbox)
    server = _stdio_model()
    server.runtime = "ruby"
    with pytest.raises(MCPError, match="runtime"):
        await pool.run(
            "t-1", "k", server, {"command": "ruby", "args": [], "action": "tools/list"}
        )


# ---------------------------------------------------------------------------
# probe 本地联调(真实子进程,零网络)
# ---------------------------------------------------------------------------

DUMMY_PY = r"""import json
import os
import sys

TOOLS = [{"name": "echo", "description": "回显", "inputSchema": {"type": "object"}}]


def main():
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except ValueError:
            continue
        method = message.get("method")
        if method == "notifications/initialized":
            # 故意制造噪声:stdout 非 JSON 行 + stderr 日志,probe 必须跳过
            sys.stdout.write("initialized\n")
            sys.stdout.flush()
            sys.stderr.write("notice\n")
            sys.stderr.flush()
            continue
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            env = os.environ.get("BEAUTY_TEST_ENV", "")
            text = "echo:%s env:%s" % (arguments.get("text"), env)
            result = {"content": [{"type": "text", "text": text}]}
        else:
            reply = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32601, "message": "method not found"},
            }
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
            continue
        reply = {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


main()
"""

SLOW_PY = r"""import time

time.sleep(30)
"""

DUMMY_JS = r"""const readline = require("readline");

const TOOLS = [{ name: "echo", description: "回显", inputSchema: { type: "object" } }];
const rl = readline.createInterface({ input: process.stdin });

rl.on("line", (line) => {
  const text = line.trim();
  if (!text) {
    return;
  }
  let message;
  try {
    message = JSON.parse(text);
  } catch (exc) {
    return;
  }
  const method = message.method;
  if (method === "notifications/initialized") {
    process.stderr.write("notice\n");
    return;
  }
  let result;
  if (method === "initialize") {
    result = { protocolVersion: "2025-03-26", capabilities: {} };
  } else if (method === "tools/list") {
    result = { tools: TOOLS };
  } else if (method === "tools/call") {
    const params = message.params || {};
    const args = params.arguments || {};
    const env = process.env.BEAUTY_TEST_ENV || "";
    result = {
      content: [{ type: "text", text: "echo:" + args.text + " env:" + env }],
    };
  } else {
    process.stdout.write(
      JSON.stringify({
        jsonrpc: "2.0",
        id: message.id,
        error: { code: -32601, message: "method not found" },
      }) + "\n"
    );
    return;
  }
  process.stdout.write(
    JSON.stringify({ jsonrpc: "2.0", id: message.id, result: result }) + "\n"
  );
});
"""


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _run_probe(
    executable: str, probe_path: Path, request: dict[str, Any]
) -> dict[str, Any]:
    completed = subprocess.run(
        [executable, str(probe_path)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip().startswith(MARKER)
    ]
    assert lines, (
        f"probe 没有输出 marker;stdout={completed.stdout} stderr={completed.stderr}"
    )
    payload = json.loads(lines[-1][len(MARKER) :])
    assert completed.returncode == 0
    return payload


def test_probe_py_lists_and_calls(tmp_path: Path) -> None:
    probe = _write(tmp_path, "probe.py", PROBE_PY)
    dummy = _write(tmp_path, "dummy.py", DUMMY_PY)
    base = {
        "command": sys.executable,
        "args": [str(dummy)],
        "env": {"BEAUTY_TEST_ENV": "42"},
        "timeout": 10,
    }
    listed = _run_probe(sys.executable, probe, {**base, "action": "tools/list"})
    assert listed["ok"] is True
    assert listed["result"]["tools"][0]["name"] == "echo"

    called = _run_probe(
        sys.executable,
        probe,
        {
            **base,
            "action": "tools/call",
            "tool": "echo",
            "arguments": {"text": "hi"},
        },
    )
    assert called["ok"] is True
    assert called["result"]["content"][0]["text"] == "echo:hi env:42"


def test_probe_py_reports_spawn_failure(tmp_path: Path) -> None:
    probe = _write(tmp_path, "probe.py", PROBE_PY)
    payload = _run_probe(
        sys.executable,
        probe,
        {
            "command": str(tmp_path / "not-a-real-binary"),
            "args": [],
            "timeout": 5,
            "action": "tools/list",
        },
    )
    assert payload["ok"] is False
    assert payload["error"]


def test_probe_py_times_out(tmp_path: Path) -> None:
    probe = _write(tmp_path, "probe.py", PROBE_PY)
    dummy = _write(tmp_path, "slow.py", SLOW_PY)
    payload = _run_probe(
        sys.executable,
        probe,
        {
            "command": sys.executable,
            "args": [str(dummy)],
            "timeout": 0.5,
            "action": "tools/list",
        },
    )
    assert payload["ok"] is False
    assert "超时" in payload["error"]


def test_probe_js_lists_and_calls(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 不可用")
    probe = _write(tmp_path, "probe.js", PROBE_JS)
    dummy = _write(tmp_path, "dummy.js", DUMMY_JS)
    base = {
        "command": node,
        "args": [str(dummy)],
        "env": {"BEAUTY_TEST_ENV": "42"},
        "timeout": 10,
    }
    listed = _run_probe(node, probe, {**base, "action": "tools/list"})
    assert listed["ok"] is True
    assert listed["result"]["tools"][0]["name"] == "echo"

    called = _run_probe(
        node,
        probe,
        {
            **base,
            "action": "tools/call",
            "tool": "echo",
            "arguments": {"text": "hi"},
        },
    )
    assert called["ok"] is True
    assert called["result"]["content"][0]["text"] == "echo:hi env:42"
