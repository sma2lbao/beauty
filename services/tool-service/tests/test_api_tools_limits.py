"""运行预算、写配额与 fetch 开关:自定义 app 装配下的边界行为。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from common.service_testkit import app_client
from fastapi.testclient import TestClient

from tests.conftest import (
    TENANT,
    FakeKnowledgeClient,
    FakeSandboxProvider,
    _settings,
    read_envelope,
    verifier,
)
from tool_service.main import create_app
from tool_service.services.executors.fetch import FetchClient
from tool_service.services.executors.http import HttpToolExecutor


def _execute(
    client: TestClient,
    payload: dict[str, Any],
    *,
    tool: str = "read",
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        f"/v1/tools/{tool}:execute", json=payload, headers=headers or TENANT
    )


def _custom_client(
    tmp_path: Path, provider: FakeSandboxProvider, **overrides: Any
) -> TestClient:
    app = create_app(
        _settings(tmp_path, **overrides),
        verifier(),
        sandbox_provider=provider,
    )
    return TestClient(app)


def test_total_call_budget_is_enforced(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    with _custom_client(tmp_path, provider, max_tool_calls_per_run=0) as client:
        resp = _execute(
            client,
            {
                "tool_call_id": "call-budget",
                "arguments": {"path": "a.txt"},
                "run_id": "run-budget",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == "tool.budget_exceeded"
    assert provider.exec_calls == []


def test_write_quota_is_enforced(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = read_envelope()
    with _custom_client(tmp_path, provider, max_writes_per_run=0) as client:
        resp = _execute(
            client,
            {
                "tool_call_id": "call-quota",
                "arguments": {"path": "a.txt", "content": "x"},
                "approved": True,
                "run_id": "run-quota",
            },
            tool="write",
        )

    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == "tool.budget_exceeded"
    assert provider.exec_calls == []


def _mock_fetcher(handler: Any) -> FetchClient:
    """注入 MockTransport 与固定公网解析结果,零网络。"""

    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    return FetchClient(transport=httpx.MockTransport(handler), resolver=resolver)


def test_fetch_is_hidden_until_enabled(client: TestClient) -> None:
    """能力即权限边界:未开 fetch 时目录不暴露,执行也明确报不可用。"""

    names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
    assert "fetch" not in names

    missing = client.get("/v1/tools/fetch")
    assert missing.status_code == 404
    assert missing.json()["code"] == "tool.not_found"

    resp = _execute(
        client,
        {
            "tool_call_id": "call-fetch-off",
            "arguments": {"url": "https://example.com/"},
        },
        tool="fetch",
    )
    # 执行入口与目录一致 404:关闭的工具不是“存在但不可用”,而是不存在。
    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.not_found"


def test_fetch_is_listed_and_executed_when_enabled(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"# ai-infra-book", headers={"content-type": "text/plain"}
        )

    fetcher = _mock_fetcher(handler)
    app = create_app(
        _settings(tmp_path, fetch_enabled=True),
        verifier(),
        sandbox_provider=FakeSandboxProvider(),
        http_executor=HttpToolExecutor(
            knowledge=FakeKnowledgeClient(), fetcher=fetcher
        ),
    )
    with app_client(app) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
        assert "fetch" in names

        resp = _execute(
            client,
            {
                "tool_call_id": "call-fetch-on",
                "arguments": {"url": "https://example.com/readme"},
            },
            tool="fetch",
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["text"] == "# ai-infra-book"
    assert body["final_url"] == "https://example.com/readme"
    assert body["status"] == 200
