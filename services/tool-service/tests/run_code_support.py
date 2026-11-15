"""run_code 测试共享装配:调用方上下文、ToolRunner 与 API 客户端工厂。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import ACCOUNT, FakeSandboxProvider, _settings, verifier
from tool_service.main import create_app
from tool_service.services.execution import ExecutionContext, ToolRunner


def run_code_context(**overrides: Any) -> ExecutionContext:
    values: dict[str, Any] = {
        "account_id": "t-1",
        "tool_call_id": "call-run-code",
        "conversation_id": "c-1",
        # run_code 是 DESTRUCTIVE:runner 层要求调用方已确认(API 层由审批前置)。
        "approved": True,
    }
    values.update(overrides)
    return ExecutionContext(**values)


def run_code_runner(tmp_path: Path, provider: FakeSandboxProvider) -> ToolRunner:
    return ToolRunner(
        settings=_settings(tmp_path, run_code_enabled=True), sandbox=provider
    )


def run_code_client(
    tmp_path: Path, provider: FakeSandboxProvider, **overrides: Any
) -> TestClient:
    app = create_app(
        _settings(tmp_path, run_code_enabled=True, **overrides),
        verifier(),
        sandbox_provider=provider,
    )
    return TestClient(app)


def execute(client: TestClient, payload: dict[str, Any]) -> Any:
    return client.post("/v1/tools/run_code:execute", json=payload, headers=ACCOUNT)
