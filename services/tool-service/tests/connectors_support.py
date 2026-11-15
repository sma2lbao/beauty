"""连接器测试共享装配:connector-service/Gateway/ToolRunner 替身与客户端工厂。

零网络:connector-service 用 httpx.MockTransport 打桩,沙箱/知识服务用
tests.conftest 里的内存替身,API 测试统一走 app_client。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from common.service_testkit.client import app_client
from common.tools import ToolDescriptor, ToolExecuteResponse
from fastapi.testclient import TestClient

from tests.conftest import FakeKnowledgeClient, FakeSandboxProvider, _settings, verifier
from tool_service.main import create_app
from tool_service.services.connectors import (
    CATALOG_PATH,
    ConnectorExecution,
    ConnectorGateway,
)
from tool_service.services.execution import ToolRunner
from tool_service.services.executors.http import HttpToolExecutor

SECRET = "connector-internal-secret"


def demo_payload(
    *,
    name: str = "Demo__read_document",
    executor: str = "connector",
    provider: str = "demo",
) -> dict[str, Any]:
    """内部目录返回的 ToolDescriptor payload(与 connector-service 同形)。"""

    return {
        "name": name,
        "description": "读取示例连接器文档",
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


DEMO_TOOL = ToolDescriptor.from_payload(demo_payload())


class FakeConnectorService:
    """connector-service 替身:目录 + 执行两个端点,记录原始请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.catalog_status = 200
        self.catalog_body: Any = {"tools": [demo_payload()]}
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


class FakeConnectorGateway:
    """ToolRunner/API 层面的连接器网关替身(零 HTTP)。"""

    def __init__(self, tools: tuple[ToolDescriptor, ...] = (DEMO_TOOL,)) -> None:
        self.tools_result = tools
        self.tool_calls: list[str] = []
        self.executed: list[dict[str, Any]] = []
        self.response = ToolExecuteResponse(ok=True, result={"content": "hello"})
        self.connection_id: str | None = None
        self.execute_error: BaseException | None = None
        self.closed = False

    @property
    def enabled(self) -> bool:
        return True

    async def tools(self, account_id: str) -> tuple[ToolDescriptor, ...]:
        self.tool_calls.append(account_id)
        return self.tools_result

    async def find(self, account_id: str, name: str) -> ToolDescriptor | None:
        return next((tool for tool in self.tools_result if tool.name == name), None)

    async def execute(
        self,
        account_id: str,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> ConnectorExecution:
        self.executed.append(
            {
                "account_id": account_id,
                "tool": descriptor.name,
                "arguments": arguments,
                "run_id": run_id,
            }
        )
        if self.execute_error is not None:
            raise self.execute_error
        return ConnectorExecution(self.response, connection_id=self.connection_id)

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
