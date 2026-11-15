"""run_code 工具的 runner 层行为:执行、工作目录、裁剪、超时与审计列。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from common.clients.sandbox import SandboxExecResult
from common.errors import UpstreamError

from tests.conftest import FakeSandboxProvider
from tests.run_code_support import run_code_context, run_code_runner
from tool_service.constants import (
    MAX_EXEC_STDERR_CHARS,
    MAX_EXEC_STDOUT_CHARS,
    MAX_TOOL_CHARS,
)
from tool_service.services import catalog, execution
from tool_service.services.execution import audit_columns
from tool_service.services.execution import limits as execution_limits

# ---- runner 层行为 ------------------------------------------------------


async def test_run_code_keeps_non_zero_exit_as_success(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "boom"
    provider.stderr = "trace"
    provider.exit_code = 2
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(
        run_code_context(), "run_code", {"code": "raise SystemExit(2)"}
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
    runner = run_code_runner(tmp_path, provider)
    code = 'name = "月"\nprint(f"hi {name}")\nprint("a\\nb")'

    await runner.execute(run_code_context(), "run_code", {"code": code})

    call = provider.exec_calls[0]
    assert call["argv"] == ["python3", "-"]
    assert call["stdin"] == code
    assert "print" not in " ".join(call["argv"])


async def test_run_code_ensures_sandbox_before_exec(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = run_code_runner(tmp_path, provider)

    await runner.execute(
        run_code_context(run_id="run-1"), "run_code", {"code": "print(1)"}
    )

    assert len(provider.ensured) == 1
    assert provider.ensured[0]["account_id"] == "t-1"
    assert provider.ensured[0]["run_id"] == "run-1"
    assert provider.touched == ["sbx-1"]


async def test_run_code_defaults_to_workspace_cwd(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(run_code_context(), "run_code", {"code": "print(1)"})

    assert outcome.response.result is not None
    assert outcome.response.result["cwd"] == "/workspace"
    assert provider.exec_calls[0]["cwd"] == "/workspace"


async def test_run_code_relative_cwd_is_normalized(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(
        run_code_context(), "run_code", {"code": "print(1)", "cwd": "reports"}
    )

    assert outcome.response.result is not None
    assert outcome.response.result["cwd"] == "/workspace/reports"
    assert provider.exec_calls[0]["cwd"] == "/workspace/reports"


async def test_run_code_absolute_cwd_inside_workspace(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(
        run_code_context(), "run_code", {"code": "print(1)", "cwd": "/workspace/sub/"}
    )

    assert outcome.response.result is not None
    assert outcome.response.result["cwd"] == "/workspace/sub"


async def test_run_code_rejects_cwd_escape(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(
        run_code_context(), "run_code", {"code": "print(1)", "cwd": "../etc"}
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
    runner = run_code_runner(tmp_path, provider)

    await runner.execute(
        run_code_context(), "run_code", {"code": "print(1)", "timeout_seconds": 5}
    )

    assert provider.exec_calls[0]["timeout_seconds"] == 5


async def test_run_code_accepts_code_at_max_length(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    runner = run_code_runner(tmp_path, provider)
    code = "x" * 20000

    outcome = await runner.execute(run_code_context(), "run_code", {"code": code})

    assert outcome.response.ok is True
    assert provider.exec_calls[0]["stdin"] == code


async def test_run_code_rejects_unknown_argument(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(
        run_code_context(), "run_code", {"code": "print(1)", "extra": 1}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.invalid_args"
    assert provider.exec_calls == []


# ---- 输出裁剪与超时 -----------------------------------------------------


async def test_run_code_clips_large_stdout(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "x" * 20000
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(
        run_code_context(), "run_code", {"code": "print('x')"}
    )

    result = outcome.response.result or {}
    assert len(result["stdout"]) == MAX_EXEC_STDOUT_CHARS
    assert outcome.response.truncated is True
    assert any("stdout" in note for note in result["notes"])


async def test_run_code_clips_large_stderr(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    provider.stderr = "e" * 9000
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(run_code_context(), "run_code", {"code": "raise 1"})

    result = outcome.response.result or {}
    assert len(result["stderr"]) == MAX_EXEC_STDERR_CHARS
    assert result["stderr"] == "e" * MAX_EXEC_STDERR_CHARS
    assert outcome.response.truncated is True
    assert any("stderr" in note for note in result["notes"])


async def test_run_code_escaped_output_fits_result_budget(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    # 换行在 JSON 里占两字符:裁剪后仍可能超过整包上限,需二次回缩。
    provider.stdout = "\n" * 16000
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(run_code_context(), "run_code", {"code": "print()"})

    result = outcome.response.result or {}
    assert len(json.dumps(result, ensure_ascii=False)) <= MAX_TOOL_CHARS
    assert outcome.response.truncated is True
    assert result["notes"]


async def test_run_code_propagates_provider_truncated(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    provider.truncated = True
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(run_code_context(), "run_code", {"code": "print(1)"})

    assert outcome.response.ok is True
    assert outcome.response.truncated is True


async def test_run_code_propagates_provider_duration(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = ""
    provider.duration_ms = 42
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(run_code_context(), "run_code", {"code": "print(1)"})

    result = outcome.response.result or {}
    assert result["duration_ms"] == 42
    assert outcome.response.duration_ms == 42


async def test_run_code_upstream_error_is_retryable(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exec_error = UpstreamError("sandbox.upstream_error", "down")
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(run_code_context(), "run_code", {"code": "print(1)"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.upstream_error"
    assert outcome.response.error.retryable is True


async def test_run_code_sandbox_timeout_maps_to_tool_timeout(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exec_error = UpstreamError("sandbox.exec_timeout", "timed out")
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(
        run_code_context(), "run_code", {"code": "while True: pass"}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"
    assert outcome.response.error.retryable is True


async def test_run_code_ensure_error_is_upstream_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.ensure_error = UpstreamError("sandbox.unreachable", "down")
    runner = run_code_runner(tmp_path, provider)

    outcome = await runner.execute(run_code_context(), "run_code", {"code": "print(1)"})

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
    runner = run_code_runner(tmp_path, _SlowRunCodeProvider())

    outcome = await runner.execute(
        run_code_context(), "run_code", {"code": "import time"}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"
    assert outcome.response.error.message.startswith("代码执行超时(")


# ---- 审计 ---------------------------------------------------------------


def test_run_code_audit_columns_keep_destructive_risk() -> None:
    context = run_code_context()
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
