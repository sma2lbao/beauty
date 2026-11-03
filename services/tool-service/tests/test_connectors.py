"""连接器工具面:目录 TTL 缓存、执行转发、执行器分派与 API 并集(零网络)。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from common.errors import NotFoundError
from common.security.internal_secret import SECRET_HEADER, TENANT_HEADER
from common.service_testkit.client import app_client
from common.tools import (
    ExecutorKind,
    ToolDescriptor,
    ToolExecuteResponse,
)
from fastapi.testclient import TestClient

from tests.conftest import (
    TENANT,
    FakeKnowledgeClient,
    FakeSandboxProvider,
    _settings,
    verifier,
)
from tests.execution_support import execution_context
from tool_service.main import create_app
from tool_service.services.connectors import CATALOG_PATH, ConnectorGateway
from tool_service.services.execution import ToolRunner, audit_columns, connector_path
from tool_service.services.executors.http import HttpToolExecutor

SECRET = "connector-internal-secret"


def feishu_payload(
    *,
    name: str = "Feishu__read_document",
    executor: str = "connector",
    provider: str = "feishu",
) -> dict[str, Any]:
    """内部目录返回的 ToolDescriptor payload(与 connector-service 同形)。"""

    return {
        "name": name,
        "description": "读取飞书文档",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
        },
        "risk": "read",
        "kind": "function",
        "executor": executor,
        "timeout_seconds": 10.0,
        "connector": {"provider": provider},
    }


FEISHU_TOOL = ToolDescriptor.from_payload(feishu_payload())


class FakeConnectorService:
    """connector-service 替身:目录 + 执行两个端点,记录原始请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.catalog_status = 200
        self.catalog_body: Any = {"tools": [feishu_payload()]}
        self.catalog_raise: BaseException | None = None
        self.exec_status = 200
        self.exec_body: Any = {"ok": True, "content": "hello"}
        self.exec_raise: BaseException | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == CATALOG_PATH:
            if self.catalog_raise is not None:
                raise self.catalog_raise
            if isinstance(self.catalog_body, str):
                return httpx.Response(self.catalog_status, text=self.catalog_body)
            return httpx.Response(self.catalog_status, json=self.catalog_body)
        if request.url.path.startswith("/v1/connectors/"):
            if self.exec_raise is not None:
                raise self.exec_raise
            if isinstance(self.exec_body, str):
                return httpx.Response(self.exec_status, text=self.exec_body)
            return httpx.Response(self.exec_status, json=self.exec_body)
        return httpx.Response(404, json={"code": "connector.not_found"})

    def catalog_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == CATALOG_PATH]

    def execute_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != CATALOG_PATH]


def make_gateway(
    service: FakeConnectorService,
    *,
    secret: str = SECRET,
    ttl_seconds: float = 30.0,
) -> ConnectorGateway:
    return ConnectorGateway(
        base_url="http://connector.test",
        internal_secret=secret,
        ttl_seconds=ttl_seconds,
        transport=httpx.MockTransport(service.handler),
    )


# ---- 目录:TTL 缓存与降级 ------------------------------------------------


async def test_tools_fetches_catalog_once_per_tenant() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service)

    first = await gateway.tools("t-1")
    second = await gateway.tools("t-1")
    other = await gateway.tools("t-2")

    assert [tool.name for tool in first] == ["Feishu__read_document"]
    assert second == first
    assert [tool.name for tool in other] == ["Feishu__read_document"]
    # 同租户命中缓存,跨租户各自拉取。
    assert len(service.catalog_requests()) == 2
    first_request, second_request = service.catalog_requests()
    assert first_request.headers[SECRET_HEADER] == SECRET
    assert first_request.headers[TENANT_HEADER] == "t-1"
    assert second_request.headers[TENANT_HEADER] == "t-2"
    await gateway.aclose()


async def test_tools_refetches_after_ttl() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service, ttl_seconds=0.0)

    await gateway.tools("t-1")
    await gateway.tools("t-1")

    assert len(service.catalog_requests()) == 2
    await gateway.aclose()


async def test_disabled_gateway_returns_empty() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service, secret="")

    assert gateway.enabled is False
    assert await gateway.tools("t-1") == ()
    assert await gateway.find("t-1", "Feishu__read_document") is None
    assert service.requests == []
    await gateway.aclose()


@pytest.mark.parametrize(
    "configure",
    [
        lambda s: setattr(s, "catalog_status", 500),
        lambda s: setattr(s, "catalog_status", 401),
        lambda s: setattr(s, "catalog_body", "not-json"),
        lambda s: setattr(s, "catalog_body", {"tools": "nope"}),
        lambda s: setattr(s, "catalog_raise", httpx.ConnectError("refused")),
    ],
)
async def test_catalog_failure_degrades_to_empty(configure: Any) -> None:
    service = FakeConnectorService()
    configure(service)
    gateway = make_gateway(service)

    assert await gateway.tools("t-1") == ()
    await gateway.aclose()


async def test_catalog_failure_is_not_cached() -> None:
    service = FakeConnectorService()
    service.catalog_status = 503
    gateway = make_gateway(service)

    assert await gateway.tools("t-1") == ()
    service.catalog_status = 200
    assert [tool.name for tool in await gateway.tools("t-1")] == [
        "Feishu__read_document"
    ]
    assert len(service.catalog_requests()) == 2
    await gateway.aclose()


async def test_catalog_skips_malformed_and_wrong_executor() -> None:
    service = FakeConnectorService()
    service.catalog_body = {
        "tools": [
            {"name": "broken"},
            feishu_payload(name="Sandboxish", executor="sandbox"),
            feishu_payload(name="Feishu__list_files"),
            feishu_payload(),
        ]
    }
    gateway = make_gateway(service)

    tools = await gateway.tools("t-1")

    assert [tool.name for tool in tools] == [
        "Feishu__list_files",
        "Feishu__read_document",
    ]
    assert tools[0].executor is ExecutorKind.CONNECTOR
    assert tools[0].connector == {"provider": "feishu"}
    await gateway.aclose()


async def test_find_matches_by_name() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service)

    found = await gateway.find("t-1", "Feishu__read_document")
    missing = await gateway.find("t-1", "Feishu__nope")

    assert found == FEISHU_TOOL
    assert missing is None
    await gateway.aclose()


# ---- 执行:转发与错误映射 ------------------------------------------------


async def test_execute_posts_envelope_and_passes_result_through() -> None:
    service = FakeConnectorService()
    service.exec_body = {"ok": True, "content": "hello", "bytes": 5}
    gateway = make_gateway(service)

    response = await gateway.execute(
        "t-1",
        FEISHU_TOOL,
        {"document_id": "doc-1"},
        run_id="run-1",
    )

    assert response.ok is True
    assert response.result == {"content": "hello", "bytes": 5}
    [request] = service.execute_requests()
    assert request.method == "POST"
    assert request.url.path == "/v1/connectors/feishu/execute"
    assert request.headers[SECRET_HEADER] == SECRET
    assert request.headers[TENANT_HEADER] == "t-1"
    assert json.loads(request.content) == {
        "tool": "Feishu__read_document",
        "arguments": {"document_id": "doc-1"},
        "run_id": "run-1",
    }
    await gateway.aclose()


async def test_execute_omits_empty_run_id() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service)

    await gateway.execute("t-1", FEISHU_TOOL, {"document_id": "doc-1"})

    [request] = service.execute_requests()
    assert "run_id" not in json.loads(request.content)
    await gateway.aclose()


async def test_execute_without_provider_is_executor_unavailable() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service)
    bare = ToolDescriptor.from_payload({**feishu_payload(), "connector": None})

    response = await gateway.execute("t-1", bare, {"document_id": "doc-1"})

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

    response = await gateway.execute("t-1", FEISHU_TOOL, {})

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

    response = await gateway.execute("t-1", FEISHU_TOOL, {})

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

    response = await gateway.execute("t-1", FEISHU_TOOL, {})

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
            "message": "请先连接飞书",
            "retryable": False,
        },
    }
    gateway = make_gateway(service)

    response = await gateway.execute("t-1", FEISHU_TOOL, {})

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "connector.auth_required"
    assert response.error.message == "请先连接飞书"
    assert response.error.retryable is False
    await gateway.aclose()


# ---- ToolRunner 分派 ----------------------------------------------------


class FakeConnectorGateway:
    """ToolRunner/API 层面的连接器网关替身(零 HTTP)。"""

    def __init__(self, tools: tuple[ToolDescriptor, ...] = (FEISHU_TOOL,)) -> None:
        self.tools_result = tools
        self.tool_calls: list[str] = []
        self.executed: list[dict[str, Any]] = []
        self.response = ToolExecuteResponse(ok=True, result={"content": "hello"})
        self.execute_error: BaseException | None = None
        self.closed = False

    @property
    def enabled(self) -> bool:
        return True

    async def tools(self, tenant_id: str) -> tuple[ToolDescriptor, ...]:
        self.tool_calls.append(tenant_id)
        return self.tools_result

    async def find(self, tenant_id: str, name: str) -> ToolDescriptor | None:
        return next((tool for tool in self.tools_result if tool.name == name), None)

    async def execute(
        self,
        tenant_id: str,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> ToolExecuteResponse:
        self.executed.append(
            {
                "tenant_id": tenant_id,
                "tool": descriptor.name,
                "arguments": arguments,
                "run_id": run_id,
            }
        )
        if self.execute_error is not None:
            raise self.execute_error
        return self.response

    async def aclose(self) -> None:
        self.closed = True


def make_connector_runner(
    tmp_path: Path, gateway: FakeConnectorGateway | None
) -> ToolRunner:
    return ToolRunner(
        settings=_settings(tmp_path),
        sandbox=FakeSandboxProvider(),
        connector=gateway,  # type: ignore[arg-type]
    )


async def test_runner_dispatches_connector_tool(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    runner = make_connector_runner(tmp_path, gateway)
    context = execution_context(run_id="run-1")

    outcome = await runner.execute(
        context, "Feishu__read_document", {"document_id": "doc-1"}
    )

    assert outcome.response.ok is True
    assert outcome.response.result == {"content": "hello"}
    assert outcome.response.tool_call_id == "call-1"
    assert outcome.response.idempotent_replay is False
    assert outcome.sandbox_id is None
    assert gateway.executed == [
        {
            "tenant_id": "t-1",
            "tool": "Feishu__read_document",
            "arguments": {"document_id": "doc-1"},
            "run_id": "run-1",
        }
    ]
    columns = audit_columns(outcome, context, {"document_id": "doc-1"}, decision="auto")
    assert columns["tool_name"] == "Feishu__read_document"
    assert columns["risk"] == "read"
    assert columns["bytes_written"] == 0
    assert columns["result_chars"] > 0


async def test_runner_passes_tool_level_failure_through(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    gateway.response = ToolExecuteResponse(ok=False, error=None)
    runner = make_connector_runner(tmp_path, gateway)

    outcome = await runner.execute(
        execution_context(), "Feishu__read_document", {"document_id": "doc-1"}
    )

    assert outcome.response.ok is False
    assert outcome.response.tool_call_id == "call-1"


async def test_runner_maps_connector_timeout(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    gateway.execute_error = TimeoutError()
    runner = make_connector_runner(tmp_path, gateway)

    outcome = await runner.execute(
        execution_context(), "Feishu__read_document", {"document_id": "doc-1"}
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
        execution_context(), "Feishu__read_document", {"document_id": "doc-1"}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.too_large"
    assert outcome.response.error.retryable is True


async def test_runner_without_gateway_cannot_resolve_connector_tool(
    tmp_path: Path,
) -> None:
    runner = make_connector_runner(tmp_path, None)

    with pytest.raises(NotFoundError) as excinfo:
        await runner.execute(
            execution_context(), "Feishu__read_document", {"document_id": "doc-1"}
        )

    assert excinfo.value.code == "tool.not_found"


# ---- API:目录并集 / 详情 / 执行审计 ------------------------------------


@contextmanager
def connector_client(
    tmp_path: Path, gateway: FakeConnectorGateway | None
) -> Iterator[TestClient]:
    app = create_app(
        _settings(tmp_path),
        token_verifier=verifier(),
        sandbox_provider=FakeSandboxProvider(),
        http_executor=HttpToolExecutor(knowledge=FakeKnowledgeClient()),
        connector_gateway=gateway,  # type: ignore[arg-type]
    )
    with app_client(app) as test_client:
        yield test_client


def test_list_tools_merges_connector_tools_for_tenant(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    with connector_client(tmp_path, gateway) as client:
        resp = client.get("/v1/tools", headers=TENANT)

    assert resp.status_code == 200
    tools = resp.json()["tools"]
    assert [tool["name"] for tool in tools][-1] == "Feishu__read_document"
    connector_tool = tools[-1]
    assert connector_tool["executor"] == "connector"
    assert connector_tool["risk"] == "read"
    assert connector_tool["connector"] == {"provider": "feishu"}
    assert gateway.tool_calls == ["t-1"]
    # 内置工具的 connector 字段显式为 null,保持 wire 形状稳定。
    assert tools[0]["connector"] is None


def test_list_tools_without_credentials_is_builtin_only(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    with connector_client(tmp_path, gateway) as client:
        anonymous = client.get("/v1/tools")
        tenant_header_only = client.get("/v1/tools", headers={"X-Tenant-Id": "t-1"})

    assert anonymous.status_code == 200
    assert "Feishu__read_document" not in [
        tool["name"] for tool in anonymous.json()["tools"]
    ]
    # 鉴权开启时 X-Tenant-Id 不算凭据,仍只返回内置目录。
    assert "Feishu__read_document" not in [
        tool["name"] for tool in tenant_header_only.json()["tools"]
    ]
    assert gateway.tool_calls == []


def test_connector_tools_absent_when_gateway_not_configured(
    client: TestClient,
) -> None:
    resp = client.get("/v1/tools", headers=TENANT)

    assert resp.status_code == 200
    assert "Feishu__read_document" not in [
        tool["name"] for tool in resp.json()["tools"]
    ]


def test_get_connector_tool_requires_tenant(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    with connector_client(tmp_path, gateway) as client:
        anonymous = client.get("/v1/tools/Feishu__read_document")
        found = client.get("/v1/tools/Feishu__read_document", headers=TENANT)

    assert anonymous.status_code == 404
    assert anonymous.json()["code"] == "tool.not_found"
    assert found.status_code == 200
    assert found.json()["name"] == "Feishu__read_document"
    assert found.json()["connector"] == {"provider": "feishu"}


def test_execute_connector_tool_audits_and_returns_envelope(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    with connector_client(tmp_path, gateway) as client:
        resp = client.post(
            "/v1/tools/Feishu__read_document:execute",
            json={
                "tool_call_id": "call-9",
                "arguments": {"document_id": "doc-1"},
                "run_id": "run-1",
            },
            headers=TENANT,
        )
        calls = client.get("/v1/tool-calls", headers=TENANT)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["content"] == "hello"
    assert body["tool_call_id"] == "call-9"
    assert body["idempotent_replay"] is False
    assert gateway.executed == [
        {
            "tenant_id": "t-1",
            "tool": "Feishu__read_document",
            "arguments": {"document_id": "doc-1"},
            "run_id": "run-1",
        }
    ]
    [record] = calls.json()["items"]
    assert record["tool_name"] == "Feishu__read_document"
    assert record["risk"] == "read"
    assert record["decision"] == "auto"
    assert record["ok"] is True
    assert record["run_id"] == "run-1"


def test_execute_unknown_connector_tool_is_404(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    with connector_client(tmp_path, gateway) as client:
        resp = client.post(
            "/v1/tools/Feishu__nope:execute",
            json={"tool_call_id": "call-9", "arguments": {}},
            headers=TENANT,
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.not_found"
    assert gateway.executed == []
