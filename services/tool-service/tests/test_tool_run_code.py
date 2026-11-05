"""run_code 工具:目录形状、开关、确认、stdin/cwd、超时与输出截断(零网络)。"""

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

from tests.conftest import ACCOUNT, FakeSandboxProvider, _settings, verifier
from tool_service.constants import (
    MAX_EXEC_STDERR_CHARS,
    MAX_EXEC_STDOUT_CHARS,
    MAX_TOOL_CHARS,
)
from tool_service.main import create_app
from tool_service.services import catalog, execution
from tool_service.services.execution import (
    ExecutionContext,
    ToolRunner,
    audit_columns,
    is_available,
)
from tool_service.services.execution import limits as execution_limits


def _context(**overrides: Any) -> ExecutionContext:
    values: dict[str, Any] = {
        "account_id": "t-1",
        "tool_call_id": "call-run-code",
        "conversation_id": "c-1",
        # run_code 是 DESTRUCTIVE:runner 层要求调用方已确认(API 层由审批前置)。
        "approved": True,
    }
    values.update(overrides)
    return ExecutionContext(**values)


def _runner(tmp_path: Path, provider: FakeSandboxProvider) -> ToolRunner:
    return ToolRunner(
        settings=_settings(tmp_path, run_code_enabled=True), sandbox=provider
    )


def _run_code_client(
    tmp_path: Path, provider: FakeSandboxProvider, **overrides: Any
) -> TestClient:
    app = create_app(
        _settings(tmp_path, run_code_enabled=True, **overrides),
        verifier(),
        sandbox_provider=provider,
    )
    return TestClient(app)


def _execute(client: TestClient, payload: dict[str, Any]) -> Any:
    return client.post("/v1/tools/run_code:execute", json=payload, headers=ACCOUNT)


# ---- 目录与开关 ---------------------------------------------------------


def test_run_code_descriptor_shape() -> None:
    descriptor = catalog.find_descriptor("run_code")
    assert descriptor is not None
    assert descriptor.enabled is True
    assert descriptor.risk is ToolRisk.DESTRUCTIVE
    assert descriptor.kind is ToolKind.FUNCTION
    assert descriptor.executor is ExecutorKind.SANDBOX
    assert descriptor.timeout_seconds == 60.0
    schema = descriptor.input_schema
    assert schema["required"] == ["code"]
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    assert properties["code"]["type"] == "string"
    assert properties["code"]["minLength"] == 1
    assert properties["code"]["maxLength"] == 20000
    assert properties["cwd"]["type"] == "string"
    assert properties["cwd"]["maxLength"] == 512
    timeout = properties["timeout_seconds"]
    assert timeout["type"] == "integer"
    assert timeout["minimum"] == 1
    assert timeout["maximum"] == 120
    assert timeout["default"] == 60


def test_run_code_is_hidden_until_enabled(client: TestClient) -> None:
    names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
    assert "run_code" not in names
    missing = client.get("/v1/tools/run_code")
    assert missing.status_code == 404
    assert missing.json()["code"] == "tool.not_found"
    resp = _execute(
        client, {"tool_call_id": "call-off", "arguments": {"code": "print(1)"}}
    )
    # 关闭的工具连执行入口都不存在(不是 200 + 执行期报不可用)。
    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.not_found"


def test_is_available_switches(tmp_path: Path) -> None:
    off = _settings(tmp_path)
    assert is_available("run_code", off) is False
    assert is_available("read", off) is True
    on = _settings(tmp_path, run_code_enabled=True)
    assert is_available("run_code", on) is True


async def test_run_code_runner_hidden_when_disabled(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = ToolRunner(settings=_settings(tmp_path), sandbox=provider)
    with pytest.raises(NotFoundError) as excinfo:
        await runner.execute(_context(), "run_code", {"code": "print(1)"})
    assert excinfo.value.code == "tool.not_found"
    assert provider.exec_calls == []


# ---- API 执行链路 -------------------------------------------------------


def test_run_code_is_listed_and_runs_when_enabled(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "hello"
    with _run_code_client(tmp_path, provider) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
        assert "run_code" in names
        descriptor = client.get("/v1/tools/run_code")
        assert descriptor.status_code == 200
        assert descriptor.json()["risk"] == "destructive"
        assert descriptor.json()["timeout_seconds"] == 60.0
        resp = _execute(
            client,
            {
                "tool_call_id": "call-on",
                "arguments": {"code": "print('hi')"},
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
    # 代码只走 stdin:argv 里只有解释器与 "-",没有任何模型内容。
    assert call["argv"] == ["python3", "-"]
    assert call["stdin"] == "print('hi')"
    assert call["cwd"] == "/workspace"
    assert call["timeout_seconds"] == 60
    assert provider.touched == ["sbx-1"]


def test_run_code_coexists_with_exec(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    with _run_code_client(tmp_path, provider, exec_enabled=True) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]

    assert "exec" in names
    assert "run_code" in names
    assert names.index("run_code") == names.index("exec") + 1


def test_run_code_requires_approval_when_enabled(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    with _run_code_client(tmp_path, provider) as client:
        resp = _execute(
            client,
            {
                "tool_call_id": "call-need",
                "arguments": {"code": "import os; os.remove('a.txt')"},
            },
        )

    assert resp.status_code == 403
    assert resp.json()["code"] == "tool.confirmation_required"
    assert provider.exec_calls == []


def test_run_code_audit_row_is_destructive(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    with _run_code_client(tmp_path, provider) as client:
        _execute(
            client,
            {
                "tool_call_id": "call-audit",
                "arguments": {"code": "print(1)"},
                "approved": True,
                "run_id": "run-code",
            },
        )
        audit = client.get("/v1/tool-calls", headers=ACCOUNT).json()["items"][0]

    assert audit["tool_name"] == "run_code"
    assert audit["risk"] == "destructive"
    assert audit["decision"] == "approved"
    assert audit["run_id"] == "run-code"


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({}, id="missing-code"),
        pytest.param({"code": ""}, id="empty-code"),
        pytest.param({"code": "x" * 20001}, id="too-long-code"),
    ],
)
def test_run_code_rejects_invalid_arguments(
    tmp_path: Path, arguments: dict[str, Any]
) -> None:
    provider = FakeSandboxProvider()
    with _run_code_client(tmp_path, provider) as client:
        resp = _execute(
            client,
            {
                "tool_call_id": "call-bad",
                "arguments": arguments,
                "approved": True,
            },
        )

    # 参数语义错误是模型可见的工具错误:200 + ok=false,且不碰沙箱。
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.invalid_args"
    assert provider.exec_calls == []
    assert provider.ensured == []


# ---- runner 层行为 ------------------------------------------------------


async def test_run_code_keeps_non_zero_exit_as_success(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "boom"
    provider.stderr = "trace"
    provider.exit_code = 2
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(
        _context(), "run_code", {"code": "raise SystemExit(2)"}
    )

    assert outcome.response.ok is True
    result = outcome.response.result or {}
    assert result["exit_code"] == 2
    assert result["stdout"] == "boom"
    assert result["stderr"] == "trace"
    assert outcome.response.truncated is False


async def test_run_code_passes_code_verbatim_on_stdin(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)
    code = 'name = "月"\nprint(f"hi {name}")\nprint("a\\nb")'

    await runner.execute(_context(), "run_code", {"code": code})

    call = provider.exec_calls[0]
    assert call["argv"] == ["python3", "-"]
    assert call["stdin"] == code
    assert "print" not in " ".join(call["argv"])


async def test_run_code_ensures_sandbox_before_exec(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)

    await runner.execute(_context(run_id="run-1"), "run_code", {"code": "print(1)"})

    assert len(provider.ensured) == 1
    assert provider.ensured[0]["account_id"] == "t-1"
    assert provider.ensured[0]["run_id"] == "run-1"
    assert provider.touched == ["sbx-1"]


async def test_run_code_defaults_to_workspace_cwd(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "run_code", {"code": "print(1)"})

    assert outcome.response.result is not None
    assert outcome.response.result["cwd"] == "/workspace"
    assert provider.exec_calls[0]["cwd"] == "/workspace"


async def test_run_code_relative_cwd_is_normalized(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(
        _context(), "run_code", {"code": "print(1)", "cwd": "reports"}
    )

    assert outcome.response.result is not None
    assert outcome.response.result["cwd"] == "/workspace/reports"
    assert provider.exec_calls[0]["cwd"] == "/workspace/reports"


async def test_run_code_absolute_cwd_inside_workspace(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(
        _context(), "run_code", {"code": "print(1)", "cwd": "/workspace/sub/"}
    )

    assert outcome.response.result is not None
    assert outcome.response.result["cwd"] == "/workspace/sub"


async def test_run_code_rejects_cwd_escape(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(
        _context(), "run_code", {"code": "print(1)", "cwd": "../etc"}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.invalid_args"
    assert "cwd" in outcome.response.error.message
    assert provider.exec_calls == []
    assert provider.ensured == []


async def test_run_code_timeout_argument_overrides_default(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)

    await runner.execute(
        _context(), "run_code", {"code": "print(1)", "timeout_seconds": 5}
    )

    assert provider.exec_calls[0]["timeout_seconds"] == 5


async def test_run_code_accepts_code_at_max_length(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = _runner(tmp_path, provider)
    code = "x" * 20000

    outcome = await runner.execute(_context(), "run_code", {"code": code})

    assert outcome.response.ok is True
    assert provider.exec_calls[0]["stdin"] == code


async def test_run_code_rejects_unknown_argument(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(
        _context(), "run_code", {"code": "print(1)", "extra": 1}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.invalid_args"
    assert provider.exec_calls == []


# ---- 输出裁剪与超时 -----------------------------------------------------


async def test_run_code_clips_large_stdout(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "x" * 20000
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "run_code", {"code": "print('x')"})

    result = outcome.response.result or {}
    assert len(result["stdout"]) == MAX_EXEC_STDOUT_CHARS
    assert outcome.response.truncated is True
    assert any("stdout" in note for note in result["notes"])


async def test_run_code_clips_large_stderr(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    provider.stderr = "e" * 9000
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "run_code", {"code": "raise 1"})

    result = outcome.response.result or {}
    assert len(result["stderr"]) == MAX_EXEC_STDERR_CHARS
    assert result["stderr"] == "e" * MAX_EXEC_STDERR_CHARS
    assert outcome.response.truncated is True
    assert any("stderr" in note for note in result["notes"])


async def test_run_code_escaped_output_fits_result_budget(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    # 换行在 JSON 里占两字符:裁剪后仍可能超过整包上限,需二次回缩。
    provider.stdout = "\n" * 16000
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "run_code", {"code": "print()"})

    result = outcome.response.result or {}
    assert len(json.dumps(result, ensure_ascii=False)) <= MAX_TOOL_CHARS
    assert outcome.response.truncated is True
    assert result["notes"]


async def test_run_code_propagates_provider_truncated(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    provider.truncated = True
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "run_code", {"code": "print(1)"})

    assert outcome.response.ok is True
    assert outcome.response.truncated is True


async def test_run_code_propagates_provider_duration(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    provider.duration_ms = 42
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "run_code", {"code": "print(1)"})

    result = outcome.response.result or {}
    assert result["duration_ms"] == 42
    assert outcome.response.duration_ms == 42


async def test_run_code_upstream_error_is_retryable(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exec_error = UpstreamError("sandbox.upstream_error", "down")
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "run_code", {"code": "print(1)"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.upstream_error"
    assert outcome.response.error.retryable is True


async def test_run_code_sandbox_timeout_maps_to_tool_timeout(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exec_error = UpstreamError("sandbox.exec_timeout", "timed out")
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "run_code", {"code": "while True: pass"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"
    assert outcome.response.error.retryable is True


async def test_run_code_ensure_error_is_upstream_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.ensure_error = UpstreamError("sandbox.unreachable", "down")
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "run_code", {"code": "print(1)"})

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.upstream_error"


class _SlowRunCodeProvider(FakeSandboxProvider):
    """ensure 立即返回、exec 挂住,用于本地超时分支。"""

    async def exec(
        self,
        account_id: str,
        sandbox_id: str,
        argv: list[str],
        *,
        cwd: str = "/workspace",
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        await asyncio.sleep(5)
        raise AssertionError("超时路径不应执行到这里")


async def test_run_code_local_timeout_returns_tool_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        execution.ToolRunner,
        "_timeout_seconds",
        lambda self, context, descriptor: 0.3,
    )
    monkeypatch.setattr(execution_limits, "SANDBOX_CALL_GRACE_SECONDS", 0.01)
    runner = _runner(tmp_path, _SlowRunCodeProvider())

    outcome = await runner.execute(_context(), "run_code", {"code": "import time"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"
    assert outcome.response.error.message.startswith("代码执行超时(")


# ---- 审计 ---------------------------------------------------------------


def test_run_code_audit_columns_keep_destructive_risk() -> None:
    context = _context()
    descriptor = catalog.find_descriptor("run_code")
    assert descriptor is not None
    outcome = execution.failure_outcome(
        descriptor, context, "tool.timeout", "代码执行超时"
    )

    columns = audit_columns(
        outcome, context, {"code": "print('hi')"}, decision="approved"
    )

    assert columns["tool_name"] == "run_code"
    assert columns["risk"] == "destructive"
    assert columns["decision"] == "approved"
    assert "print('hi')" in columns["arguments"]
