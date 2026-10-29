"""exec 工具:目录形状、开关、确认、argv/cwd、超时与输出截断(零网络)。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from common.clients.sandbox import SandboxExecResult
from common.errors import NotFoundError, UpstreamError
from common.tools import ExecutorKind, ToolKind, ToolRisk
from fastapi.testclient import TestClient

from tests.conftest import TENANT, FakeSandboxProvider, _settings, verifier
from tool_service.constants import MAX_EXEC_STDOUT_CHARS, MAX_TOOL_CHARS
from tool_service.main import create_app
from tool_service.services import catalog, execution
from tool_service.services.execution import (
    ExecutionContext,
    ToolRunner,
    audit_columns,
    is_available,
    resolve_exec_cwd,
)
from tool_service.services.execution import limits as execution_limits


def _context(**overrides: Any) -> ExecutionContext:
    values: dict[str, Any] = {
        "tenant_id": "t-1",
        "tool_call_id": "call-exec",
        "conversation_id": "c-1",
        # exec 是 DESTRUCTIVE:runner 层要求调用方已确认(API 层由审批前置)。
        "approved": True,
    }
    values.update(overrides)
    return ExecutionContext(**values)


def _runner(tmp_path: Path, provider: FakeSandboxProvider) -> ToolRunner:
    return ToolRunner(settings=_settings(tmp_path, exec_enabled=True), sandbox=provider)


def _exec_client(
    tmp_path: Path, provider: FakeSandboxProvider, **overrides: Any
) -> TestClient:
    app = create_app(
        _settings(tmp_path, exec_enabled=True, **overrides),
        verifier(),
        sandbox_provider=provider,
    )
    return TestClient(app)


def _execute(client: TestClient, payload: dict[str, Any]) -> Any:
    return client.post("/v1/tools/exec:execute", json=payload, headers=TENANT)


# ---- 目录与开关 ---------------------------------------------------------


def test_exec_descriptor_shape() -> None:
    descriptor = catalog.find_descriptor("exec")
    assert descriptor is not None
    assert descriptor.enabled is True
    assert descriptor.risk is ToolRisk.DESTRUCTIVE
    assert descriptor.kind is ToolKind.FUNCTION
    assert descriptor.executor is ExecutorKind.SANDBOX
    assert descriptor.timeout_seconds == 60.0
    schema = descriptor.input_schema
    assert schema["required"] == ["command"]
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    assert properties["command"]["maxLength"] == 4000
    assert properties["cwd"]["maxLength"] == 512
    assert properties["timeout_seconds"]["minimum"] == 1
    assert properties["timeout_seconds"]["maximum"] == 120


def test_exec_is_hidden_until_enabled(client: TestClient) -> None:
    names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
    assert "exec" not in names
    missing = client.get("/v1/tools/exec")
    assert missing.status_code == 404
    assert missing.json()["code"] == "tool.not_found"
    resp = _execute(
        client, {"tool_call_id": "call-off", "arguments": {"command": "echo hi"}}
    )
    # 关闭的工具连执行入口都不存在(不是 200 + 执行期报不可用)。
    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.not_found"


def test_is_available_switches(tmp_path: Path) -> None:
    off = _settings(tmp_path)
    assert is_available("exec", off) is False
    assert is_available("fetch", off) is False
    assert is_available("read", off) is True
    on = _settings(tmp_path, exec_enabled=True, fetch_enabled=True)
    assert is_available("exec", on) is True
    assert is_available("fetch", on) is True


async def test_exec_runner_hidden_when_disabled(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = ToolRunner(settings=_settings(tmp_path), sandbox=provider)
    with pytest.raises(NotFoundError) as excinfo:
        await runner.execute(_context(), "exec", {"command": "echo hi"})
    assert excinfo.value.code == "tool.not_found"
    assert provider.exec_calls == []


# ---- API 执行链路 -------------------------------------------------------


def test_exec_is_listed_and_runs_when_enabled(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "hello"
    with _exec_client(tmp_path, provider) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
        assert "exec" in names
        descriptor = client.get("/v1/tools/exec")
        assert descriptor.status_code == 200
        assert descriptor.json()["risk"] == "destructive"
        resp = _execute(
            client,
            {
                "tool_call_id": "call-on",
                "arguments": {"command": "echo hello"},
                "approved": True,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["exit_code"] == 0
    assert body["stdout"] == "hello"
    assert body["cwd"] == "/workspace"
    assert body["truncated"] is False
    call = provider.exec_calls[0]
    assert call["argv"] == ["/bin/sh", "-lc", "echo hello"]
    assert call["cwd"] == "/workspace"
    assert call["stdin"] is None
    assert call["timeout_seconds"] == 60
    assert provider.touched == ["sbx-1"]


def test_exec_requires_approval_when_enabled(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    with _exec_client(tmp_path, provider) as client:
        resp = _execute(
            client,
            {
                "tool_call_id": "call-need",
                "arguments": {"command": "rm -rf /tmp/x"},
            },
        )

    assert resp.status_code == 403
    assert resp.json()["code"] == "tool.confirmation_required"
    assert provider.exec_calls == []


def test_exec_audit_row_is_destructive(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    with _exec_client(tmp_path, provider) as client:
        _execute(
            client,
            {
                "tool_call_id": "call-audit",
                "arguments": {"command": "pytest -q"},
                "approved": True,
                "run_id": "run-exec",
            },
        )
        audit = client.get("/v1/tool-calls", headers=TENANT).json()["items"][0]

    assert audit["tool_name"] == "exec"
    assert audit["risk"] == "destructive"
    assert audit["decision"] == "approved"
    assert audit["run_id"] == "run-exec"


# ---- runner 层行为 ------------------------------------------------------


async def test_exec_keeps_non_zero_exit_as_success(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "boom"
    provider.stderr = "trace"
    provider.exit_code = 2
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "exec", {"command": "false"})

    assert outcome.response.ok is True
    result = outcome.response.result or {}
    assert result["exit_code"] == 2
    assert result["stdout"] == "boom"
    assert result["stderr"] == "trace"
    assert outcome.response.truncated is False


async def test_exec_relative_cwd_is_normalized(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(
        _context(), "exec", {"command": "ls", "cwd": "reports"}
    )

    assert outcome.response.result is not None
    assert outcome.response.result["cwd"] == "/workspace/reports"
    assert provider.exec_calls[0]["cwd"] == "/workspace/reports"


async def test_exec_absolute_cwd_inside_workspace(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(
        _context(), "exec", {"command": "ls", "cwd": "/workspace/sub/"}
    )

    assert outcome.response.result is not None
    assert outcome.response.result["cwd"] == "/workspace/sub"


async def test_exec_rejects_cwd_escape(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(
        _context(), "exec", {"command": "ls", "cwd": "../etc"}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.invalid_args"
    assert "cwd" in outcome.response.error.message
    assert provider.exec_calls == []
    assert provider.ensured == []


async def test_exec_timeout_argument_overrides_default(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)

    await runner.execute(
        _context(), "exec", {"command": "sleep 1", "timeout_seconds": 5}
    )

    assert provider.exec_calls[0]["timeout_seconds"] == 5


async def test_exec_clips_large_stdout(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "x" * 20000
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "exec", {"command": "cat big"})

    result = outcome.response.result or {}
    assert len(result["stdout"]) == MAX_EXEC_STDOUT_CHARS
    assert outcome.response.truncated is True
    assert any("stdout" in note for note in result["notes"])


async def test_exec_escaped_output_fits_result_budget(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    # 换行在 JSON 里占两字符:裁剪后仍可能超过整包上限,需二次回缩。
    provider.stdout = "\n" * 16000
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "exec", {"command": "yes"})

    result = outcome.response.result or {}
    assert len(json.dumps(result, ensure_ascii=False)) <= MAX_TOOL_CHARS
    assert outcome.response.truncated is True
    assert result["notes"]


async def test_exec_upstream_error_is_retryable(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exec_error = UpstreamError("sandbox.upstream_error", "down")
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "exec", {"command": "ls"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.upstream_error"
    assert outcome.response.error.retryable is True


async def test_exec_sandbox_timeout_maps_to_tool_timeout(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exec_error = UpstreamError("sandbox.exec_timeout", "timed out")
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "exec", {"command": "sleep 999"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"
    assert outcome.response.error.retryable is True


async def test_exec_ensure_error_is_upstream_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.ensure_error = UpstreamError("sandbox.unreachable", "down")
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "exec", {"command": "ls"})

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.upstream_error"


class _SlowExecProvider(FakeSandboxProvider):
    """ensure 立即返回、exec 挂住,用于本地超时分支。"""

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: list[str],
        *,
        cwd: str = "/workspace",
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        await asyncio.sleep(5)
        raise AssertionError("超时路径不应执行到这里")


async def test_exec_local_timeout_returns_tool_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        execution.ToolRunner,
        "_timeout_seconds",
        lambda self, context, descriptor: 0.3,
    )
    monkeypatch.setattr(execution_limits, "SANDBOX_CALL_GRACE_SECONDS", 0.01)
    runner = _runner(tmp_path, _SlowExecProvider())

    outcome = await runner.execute(_context(), "exec", {"command": "sleep 10"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"


def test_exec_audit_columns_keep_destructive_risk() -> None:
    context = _context()
    descriptor = catalog.find_descriptor("exec")
    assert descriptor is not None
    outcome = execution.failure_outcome(
        descriptor, context, "tool.timeout", "命令执行超时"
    )

    columns = audit_columns(
        outcome, context, {"command": "make test"}, decision="approved"
    )

    assert columns["tool_name"] == "exec"
    assert columns["risk"] == "destructive"
    assert columns["decision"] == "approved"
    assert "make test" in columns["arguments"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "/workspace"),
        ("", "/workspace"),
        ("reports", "/workspace/reports"),
        ("./reports/../logs", "/workspace/logs"),
        ("/workspace/sub", "/workspace/sub"),
        ("/workspace", "/workspace"),
        ("../etc", None),
        ("/etc/passwd", None),
        ("/workspace/../etc", None),
        ("reports\\..\\etc", None),
        (123, None),
        ("/workspace/" + "a" * 600, None),
    ],
)
def test_resolve_exec_cwd(raw: Any, expected: str | None) -> None:
    assert resolve_exec_cwd(raw) == expected
