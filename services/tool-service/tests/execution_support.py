"""execution 测试共享装配:调用方上下文与 ToolRunner 工厂。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.conftest import FakeSandboxProvider, _settings
from tool_service.services.execution import ExecutionContext, ToolRunner
from tool_service.services.executors.http import HttpToolExecutor


def execution_context(**overrides: Any) -> ExecutionContext:
    values: dict[str, Any] = {
        "tenant_id": "t-1",
        "tool_call_id": "call-1",
        "conversation_id": "c-1",
    }
    values.update(overrides)
    return ExecutionContext(**values)


def make_runner(
    tmp_path: Path,
    provider: FakeSandboxProvider,
    *,
    http: HttpToolExecutor | None = None,
) -> ToolRunner:
    return ToolRunner(settings=_settings(tmp_path), sandbox=provider, http=http)
