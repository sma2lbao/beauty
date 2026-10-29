"""幂等摘要与 tool_calls 审计列映射(零网络)。"""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import FakeSandboxProvider
from tests.execution_support import execution_context, make_runner
from tool_service.services.execution import (
    arguments_digest,
    audit_columns,
)


def test_arguments_digest_ignores_key_order() -> None:
    assert arguments_digest({"b": 2, "a": 1}) == arguments_digest({"a": 1, "b": 2})
    assert arguments_digest({"a": 1}) != arguments_digest({"a": 2})


async def test_audit_columns_for_success(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)
    context = execution_context(conversation_id="c-1")
    arguments = {"path": "a.txt"}

    outcome = await runner.execute(context, "read", arguments)
    columns = audit_columns(outcome, context, arguments, decision="auto")

    assert columns["tool_name"] == "read"
    assert columns["tool_version"] == outcome.descriptor.version
    assert columns["risk"] == "read"
    assert columns["decision"] == "auto"
    assert columns["ok"] is True
    assert columns["error_code"] is None
    assert columns["duration_ms"] == 5
    assert columns["result_chars"] > 0
    assert columns["bytes_written"] == 0
    assert columns["truncated"] is False
    assert json.loads(columns["paths"]) == ["a.txt"]
    assert columns["sha256_before"] is None
    assert columns["sha256_after"] is None
    assert columns["sandbox_id"] == "sbx-1"
    assert columns["arguments_sha256"] == arguments_digest(arguments)
    assert json.loads(columns["arguments"]) == arguments


async def test_audit_columns_for_failure(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)
    context = execution_context()

    outcome = await runner.execute(context, "read", {})
    columns = audit_columns(outcome, context, {}, decision="auto")

    assert columns["ok"] is False
    assert columns["error_code"] == "tool.invalid_args"
    assert columns["result_chars"] == 0
    assert json.loads(columns["paths"]) == []
    assert columns["sandbox_id"] is None


async def test_audit_paths_are_capped(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    matches = [{"path": f"file-{index}.py", "line": 1} for index in range(30)]
    provider.stdout = json.dumps({"ok": True, "matches": matches})
    runner = make_runner(tmp_path, provider)
    context = execution_context()
    arguments = {"pattern": "x"}

    outcome = await runner.execute(context, "grep", arguments)
    columns = audit_columns(outcome, context, arguments, decision="auto")

    assert len(json.loads(columns["paths"])) == 20
