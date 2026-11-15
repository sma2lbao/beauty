"""run_code 工具:目录形状、开关、确认与 API 执行链路(零网络)。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from common.errors import NotFoundError
from common.tools import ExecutorKind, ToolKind, ToolRisk
from fastapi.testclient import TestClient

from tests.conftest import ACCOUNT, FakeSandboxProvider, _settings
from tests.run_code_support import (
    execute,
    run_code_client,
    run_code_context,
)
from tool_service.services import catalog
from tool_service.services.execution import ToolRunner, is_available

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
    resp = execute(
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
        await runner.execute(run_code_context(), "run_code", {"code": "print(1)"})
    assert excinfo.value.code == "tool.not_found"
    assert provider.exec_calls == []


# ---- API 执行链路 -------------------------------------------------------


def test_run_code_is_listed_and_runs_when_enabled(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "hello"
    with run_code_client(tmp_path, provider) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
        assert "run_code" in names
        descriptor = client.get("/v1/tools/run_code")
        assert descriptor.status_code == 200
        assert descriptor.json()["risk"] == "destructive"
        assert descriptor.json()["timeout_seconds"] == 60.0
        resp = execute(
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
    with run_code_client(tmp_path, provider, exec_enabled=True) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]

    assert "exec" in names
    assert "run_code" in names
    assert names.index("run_code") == names.index("exec") + 1


def test_run_code_requires_approval_when_enabled(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    with run_code_client(tmp_path, provider) as client:
        resp = execute(
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
    with run_code_client(tmp_path, provider) as client:
        execute(
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
    with run_code_client(tmp_path, provider) as client:
        resp = execute(
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
