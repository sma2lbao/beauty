"""stdio MCP 工具注入(tool_specs)与 StdioSandboxPool 生命周期。"""

import json

import pytest
from common.errors import UpstreamError

from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services.mcp_client import MCPError
from agent_orchestrator.services.mcp_stdio import (
    MARKER,
    PROBE_JS,
    PROBE_PY,
    StdioSandboxPool,
)
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from tests.support.mcp_stdio import (
    FakePool,
    FakeSandbox,
    _ok_stdout,
    _patch_repo,
    _stdio_model,
)


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
