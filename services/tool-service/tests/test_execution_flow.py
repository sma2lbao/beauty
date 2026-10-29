"""执行主流程:目录分派、能力开关、风险下限与交互工具的归一(零网络)。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from common.errors import NotFoundError, PermissionDeniedError
from common.tools import ExecutorKind, ToolRisk

from tests.conftest import FakeSandboxProvider
from tests.execution_support import execution_context, make_runner
from tool_service.services import catalog
from tool_service.services.execution import (
    audit_columns,
)


async def test_execute_read_returns_envelope(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is True
    assert outcome.response.tool_call_id == "call-1"
    assert outcome.response.idempotent_replay is False
    assert outcome.sandbox_id == "sbx-1"
    assert outcome.response.duration_ms == 5
    assert provider.ensured[0]["scope_key"] == "conversation:c-1"
    assert provider.exec_calls[0]["argv"][0] == "python3"
    request = json.loads(provider.exec_calls[0]["stdin"])
    assert request["tool"] == "read"
    assert request["arguments"] == {"path": "a.txt"}
    assert "policy" in request
    assert provider.touched == ["sbx-1"]


async def test_execute_resolves_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)
    monkeypatch.setitem(catalog.ALIASES, "legacy_local_read", "read")

    outcome = await runner.execute(
        execution_context(), "legacy_local_read", {"path": "a.txt"}
    )

    assert outcome.descriptor.name == "read"
    assert json.loads(provider.exec_calls[0]["stdin"])["tool"] == "read"


async def test_invalid_arguments_return_error_envelope(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.invalid_args"
    assert provider.exec_calls == []


async def test_unknown_tool_raises_not_found(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, FakeSandboxProvider())

    with pytest.raises(NotFoundError) as raised:
        await runner.execute(execution_context(), "nope", {})

    assert raised.value.code == "tool.not_found"


async def test_disabled_tool_raises_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, enabled=False)
    monkeypatch.setattr(catalog, "find_descriptor", lambda name: descriptor)
    runner = make_runner(tmp_path, FakeSandboxProvider())

    with pytest.raises(NotFoundError) as raised:
        await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert raised.value.code == "tool.not_found"


async def test_write_requires_approval(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)

    with pytest.raises(PermissionDeniedError) as raised:
        await runner.execute(
            execution_context(), "write", {"path": "a.txt", "content": "x"}
        )

    assert raised.value.code == "tool.confirmation_required"
    assert provider.exec_calls == []


async def test_write_with_approval_merges_internal_arguments(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps(
        {
            "ok": True,
            "path": "a.txt",
            "created": False,
            "changed": True,
            "bytes": 7,
            "lines": 1,
            "sha256_before": "0" * 64,
            "sha256_after": "1" * 64,
            "notes": [],
        }
    )
    runner = make_runner(tmp_path, provider)
    context = execution_context(approved=True)
    arguments = {"path": "a.txt", "content": "hello"}

    outcome = await runner.execute(
        context,
        "write",
        arguments,
        internal_arguments={"_expected_read_sha256": "0" * 64},
    )
    request = json.loads(provider.exec_calls[0]["stdin"])
    columns = audit_columns(outcome, context, arguments, decision="approved")

    assert outcome.response.ok is True
    assert request["arguments"]["_expected_read_sha256"] == "0" * 64
    # 审计只记录调用方参数,内部参数不落库
    assert json.loads(columns["arguments"]) == arguments
    assert columns["bytes_written"] == 7


async def test_destructive_requires_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, risk=ToolRisk.DESTRUCTIVE)
    monkeypatch.setattr(catalog, "find_descriptor", lambda name: descriptor)
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)

    with pytest.raises(PermissionDeniedError) as raised:
        await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert raised.value.code == "tool.confirmation_required"
    assert provider.exec_calls == []


async def test_destructive_with_approval_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, risk=ToolRisk.DESTRUCTIVE)
    monkeypatch.setattr(catalog, "find_descriptor", lambda name: descriptor)
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(
        execution_context(approved=True), "read", {"path": "a.txt"}
    )

    assert outcome.response.ok is True


async def test_http_executor_is_not_wired_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, executor=ExecutorKind.HTTP)
    monkeypatch.setattr(catalog, "find_descriptor", lambda name: descriptor)
    runner = make_runner(tmp_path, FakeSandboxProvider())

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.executor_unavailable"


async def test_none_executor_is_not_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, executor=ExecutorKind.NONE)
    monkeypatch.setattr(catalog, "find_descriptor", lambda name: descriptor)
    runner = make_runner(tmp_path, FakeSandboxProvider())

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.not_supported"
