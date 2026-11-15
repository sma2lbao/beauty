"""连接器工具面:ToolRunner 分派与审计列(零网络)。"""

from __future__ import annotations

from pathlib import Path

import pytest
from common.errors import NotFoundError
from common.tools import ToolExecuteResponse

from tests.connectors_support import FakeConnectorGateway, make_connector_runner
from tests.execution_support import execution_context
from tool_service.services.execution import audit_columns, connector_path

# ---- ToolRunner 分派 ----------------------------------------------------


async def test_runner_dispatches_connector_tool(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    runner = make_connector_runner(tmp_path, gateway)
    context = execution_context(run_id="run-1")

    outcome = await runner.execute(
        context, "Demo__read_document", {"document_id": "doc-1"}
    )

    assert outcome.response.ok is True
    assert outcome.response.result == {"content": "hello"}
    assert outcome.response.tool_call_id == "call-1"
    assert outcome.response.idempotent_replay is False
    assert outcome.sandbox_id is None
    assert gateway.executed == [
        {
            "account_id": "t-1",
            "tool": "Demo__read_document",
            "arguments": {"document_id": "doc-1"},
            "run_id": "run-1",
        }
    ]
    columns = audit_columns(outcome, context, {"document_id": "doc-1"}, decision="auto")
    assert outcome.connector_connection_id is None
    assert columns["tool_name"] == "Demo__read_document"
    assert columns["risk"] == "read"
    assert columns["bytes_written"] == 0
    assert columns["result_chars"] > 0
    assert columns["connection_id"] is None


async def test_runner_carries_connection_id_into_audit(tmp_path: Path) -> None:
    """gateway 带回的连接 id 必须原样进入审计列(C2.4 端到端)。"""

    gateway = FakeConnectorGateway()
    gateway.connection_id = "conn-demo-9"
    runner = make_connector_runner(tmp_path, gateway)
    context = execution_context(run_id="run-1")

    outcome = await runner.execute(
        context, "Demo__read_document", {"document_id": "doc-1"}
    )
    columns = audit_columns(outcome, context, {"document_id": "doc-1"}, decision="auto")

    assert outcome.connector_connection_id == "conn-demo-9"
    assert columns["connection_id"] == "conn-demo-9"


async def test_runner_passes_tool_level_failure_through(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    gateway.response = ToolExecuteResponse(ok=False, error=None)
    runner = make_connector_runner(tmp_path, gateway)

    outcome = await runner.execute(
        execution_context(), "Demo__read_document", {"document_id": "doc-1"}
    )

    assert outcome.response.ok is False
    assert outcome.response.tool_call_id == "call-1"


async def test_runner_maps_connector_timeout(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    gateway.execute_error = TimeoutError()
    runner = make_connector_runner(tmp_path, gateway)

    outcome = await runner.execute(
        execution_context(), "Demo__read_document", {"document_id": "doc-1"}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.connector_timeout"
    assert outcome.response.error.retryable is True


async def test_runner_rejects_too_large_connector_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connector_path, "MAX_TOOL_CHARS", 5)
    gateway = FakeConnectorGateway()
    gateway.response = ToolExecuteResponse(ok=True, result={"content": "too long"})
    runner = make_connector_runner(tmp_path, gateway)

    outcome = await runner.execute(
        execution_context(), "Demo__read_document", {"document_id": "doc-1"}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.too_large"
    assert outcome.response.error.retryable is True
    assert outcome.connector_connection_id is None


async def test_runner_too_large_keeps_connection_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """结果过大被拦下时也要保留连接 id,审计才能回溯到具体连接。"""

    monkeypatch.setattr(connector_path, "MAX_TOOL_CHARS", 5)
    gateway = FakeConnectorGateway()
    gateway.connection_id = "conn-demo-9"
    gateway.response = ToolExecuteResponse(ok=True, result={"content": "too long"})
    runner = make_connector_runner(tmp_path, gateway)

    outcome = await runner.execute(
        execution_context(), "Demo__read_document", {"document_id": "doc-1"}
    )

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.too_large"
    assert outcome.connector_connection_id == "conn-demo-9"


async def test_runner_without_gateway_cannot_resolve_connector_tool(
    tmp_path: Path,
) -> None:
    runner = make_connector_runner(tmp_path, None)

    with pytest.raises(NotFoundError) as excinfo:
        await runner.execute(
            execution_context(), "Demo__read_document", {"document_id": "doc-1"}
        )

    assert excinfo.value.code == "tool.not_found"
