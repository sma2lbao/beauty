"""连接器工具面:执行转发、错误映射与 connection_id 提取(零网络)。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from common.security.internal_secret import ACCOUNT_HEADER, SECRET_HEADER
from common.tools import ToolDescriptor

from tests.connectors_support import (
    DEMO_TOOL,
    SECRET,
    FakeConnectorService,
    demo_payload,
    make_gateway,
)

# ---- 执行:转发与错误映射 ------------------------------------------------


async def test_execute_posts_envelope_and_passes_result_through() -> None:
    service = FakeConnectorService()
    service.exec_body = {"ok": True, "content": "hello", "bytes": 5}
    gateway = make_gateway(service)

    execution = await gateway.execute(
        "t-1",
        DEMO_TOOL,
        {"document_id": "doc-1"},
        run_id="run-1",
    )

    assert execution.connection_id is None
    response = execution.response
    assert response.ok is True
    assert response.result == {"content": "hello", "bytes": 5}
    [request] = service.execute_requests()
    assert request.method == "POST"
    assert request.url.path == "/v1/connectors/demo/execute"
    assert request.headers[SECRET_HEADER] == SECRET
    assert request.headers[ACCOUNT_HEADER] == "t-1"
    assert json.loads(request.content) == {
        "tool": "Demo__read_document",
        "arguments": {"document_id": "doc-1"},
        "run_id": "run-1",
    }
    await gateway.aclose()


async def test_execute_omits_empty_run_id() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service)

    execution = await gateway.execute("t-1", DEMO_TOOL, {"document_id": "doc-1"})

    assert execution.connection_id is None
    [request] = service.execute_requests()
    assert "run_id" not in json.loads(request.content)
    await gateway.aclose()


async def test_execute_without_provider_is_executor_unavailable() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service)
    bare = ToolDescriptor.from_payload({**demo_payload(), "connector": None})

    execution = await gateway.execute("t-1", bare, {"document_id": "doc-1"})

    assert execution.connection_id is None
    response = execution.response
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.executor_unavailable"
    assert service.requests == []
    await gateway.aclose()


@pytest.mark.parametrize(
    ("status", "retryable", "message_part"),
    [
        (404, False, "没有可用连接"),
        (401, False, "拒绝"),
        (422, False, "拒绝执行"),
        (503, True, "返回 503"),
    ],
)
async def test_execute_maps_status_errors(
    status: int, retryable: bool, message_part: str
) -> None:
    service = FakeConnectorService()
    service.exec_status = status
    service.exec_body = "down"
    gateway = make_gateway(service)

    execution = await gateway.execute("t-1", DEMO_TOOL, {})

    response = execution.response
    assert execution.connection_id is None
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.connector_unavailable"
    assert response.error.retryable is retryable
    assert message_part in response.error.message
    assert "down" in response.error.hint
    await gateway.aclose()


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (httpx.ReadTimeout("slow"), "tool.connector_timeout", True),
        (httpx.ConnectError("refused"), "tool.connector_unavailable", True),
    ],
)
async def test_execute_maps_transport_errors(
    error: BaseException, code: str, retryable: bool
) -> None:
    service = FakeConnectorService()
    service.exec_raise = error
    gateway = make_gateway(service)

    execution = await gateway.execute("t-1", DEMO_TOOL, {})

    assert execution.connection_id is None
    response = execution.response
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == code
    assert response.error.retryable is retryable
    await gateway.aclose()


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        [{"ok": True}],
        {"content": "missing-ok"},
        {"ok": "yes"},
    ],
)
async def test_execute_maps_bad_envelopes(body: Any) -> None:
    service = FakeConnectorService()
    service.exec_body = body
    gateway = make_gateway(service)

    execution = await gateway.execute("t-1", DEMO_TOOL, {})

    assert execution.connection_id is None
    response = execution.response
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.connector_unavailable"
    assert response.error.retryable is True
    await gateway.aclose()


async def test_execute_passes_through_tool_level_failure() -> None:
    service = FakeConnectorService()
    service.exec_body = {
        "ok": False,
        "error": {
            "code": "connector.auth_required",
            "message": "请先连接示例连接器",
            "retryable": False,
        },
    }
    gateway = make_gateway(service)

    execution = await gateway.execute("t-1", DEMO_TOOL, {})

    assert execution.connection_id is None
    response = execution.response
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "connector.auth_required"
    assert response.error.message == "请先连接示例连接器"
    assert response.error.retryable is False
    await gateway.aclose()


# ---- 执行:connection_id 提取与剥离(C2.4 审计) --------------------------


async def test_execute_extracts_connection_id_and_strips_it_from_result() -> None:
    service = FakeConnectorService()
    service.exec_body = {
        "ok": True,
        "content": "hello",
        "connection_id": "conn-demo-1",
    }
    gateway = make_gateway(service)

    execution = await gateway.execute("t-1", DEMO_TOOL, {"document_id": "doc-1"})

    assert execution.connection_id == "conn-demo-1"
    # connection_id 只进审计:不能泄漏到模型可见的 result。
    assert execution.response.result == {"content": "hello"}
    await gateway.aclose()


async def test_execute_failure_envelope_still_carries_connection_id() -> None:
    service = FakeConnectorService()
    service.exec_body = {
        "ok": False,
        "connection_id": "conn-demo-1",
        "error": {
            "code": "connector.auth_required",
            "message": "请先连接示例连接器",
            "retryable": False,
        },
    }
    gateway = make_gateway(service)

    execution = await gateway.execute("t-1", DEMO_TOOL, {})

    assert execution.connection_id == "conn-demo-1"
    assert execution.response.ok is False
    assert execution.response.result in (None, {})
    await gateway.aclose()


@pytest.mark.parametrize("raw", [None, "", "   ", 42, {"id": "x"}])
async def test_execute_ignores_non_string_connection_id(raw: Any) -> None:
    service = FakeConnectorService()
    service.exec_body = {"ok": True, "content": "hello", "connection_id": raw}
    gateway = make_gateway(service)

    execution = await gateway.execute("t-1", DEMO_TOOL, {})

    assert execution.connection_id is None
    # 非法值同样剥离,避免混进模型可见的结果。
    assert execution.response.result == {"content": "hello"}
    await gateway.aclose()
