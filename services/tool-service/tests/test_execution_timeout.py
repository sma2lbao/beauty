"""exec 超时与上游失败:客户端超时口径、ensure 超时与 touch 失败(零网络)。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from common.clients.sandbox import SandboxExecResult
from common.errors import UpstreamError

from tests.conftest import FakeSandboxProvider
from tests.execution_support import execution_context, make_runner
from tool_service.services import execution
from tool_service.services.execution import limits as execution_limits


class SlowExecProvider(FakeSandboxProvider):
    """ensure 立即返回、exec 挂住,用于执行超时分支。"""

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


def _shrink_timeout(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.3) -> None:
    monkeypatch.setattr(
        execution.ToolRunner,
        "_timeout_seconds",
        lambda self, context, descriptor: seconds,
    )
    monkeypatch.setattr(execution_limits, "SANDBOX_CALL_GRACE_SECONDS", 0.01)


async def test_exec_timeout_returns_retryable_error_and_touches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shrink_timeout(monkeypatch)
    provider = SlowExecProvider()
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"
    assert outcome.response.error.retryable is True
    assert provider.touched == ["sbx-1"]


class SlowEnsureProvider(FakeSandboxProvider):
    async def ensure(
        self, tenant_id: str, *, scope_key: str, run_id: str | None = None
    ) -> str:
        await asyncio.sleep(5)
        raise AssertionError("超时路径不应执行到这里")


async def test_ensure_timeout_returns_retryable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shrink_timeout(monkeypatch)
    runner = make_runner(tmp_path, SlowEnsureProvider())

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"
    assert "沙箱" in outcome.response.error.message


async def test_touch_failure_does_not_fail_call(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.touch_error = UpstreamError("sandbox.unreachable", "down")
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is True


async def test_ensure_upstream_error_propagates(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.ensure_error = UpstreamError("sandbox.unreachable", "down")
    runner = make_runner(tmp_path, provider)

    with pytest.raises(UpstreamError):
        await runner.execute(execution_context(), "read", {"path": "a.txt"})


async def test_exec_upstream_error_propagates_and_touches(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exec_error = UpstreamError("sandbox.upstream_error", "boom")
    runner = make_runner(tmp_path, provider)

    with pytest.raises(UpstreamError):
        await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert provider.touched == ["sbx-1"]


async def test_request_timeout_override_is_forwarded(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)

    await runner.execute(
        execution_context(timeout_seconds=2), "read", {"path": "a.txt"}
    )

    assert provider.exec_calls[0]["timeout_seconds"] == 2


async def test_workspace_scope_uses_explicit_scope_key(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)

    await runner.execute(
        execution_context(conversation_id=None, run_id="r-9"), "read", {"path": "a.txt"}
    )

    assert provider.ensured[0]["scope_key"] == "run:r-9"
    assert provider.ensured[0]["run_id"] == "r-9"
