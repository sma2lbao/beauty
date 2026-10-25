"""stdio 型 MCP(P2)测试共享夹具:schema 常量、FakePool 与 scripted sandbox。"""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services.mcp_stdio import MARKER
from agent_orchestrator.services.sandbox import SandboxExecResult, SandboxInfo
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
