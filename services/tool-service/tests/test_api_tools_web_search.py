"""web_search 的目录可见性与执行端到端:开关/密钥/提供方三件套决定可见性。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from tests.conftest import (
    ACCOUNT,
    FakeSandboxProvider,
    _settings,
    verifier,
)
from tool_service.main import create_app
from tool_service.services.executors.http import HttpToolExecutor
from tool_service.services.executors.web_search import SearchClient

TOOL_NAME = "web_search"


def _custom_client(
    tmp_path: Path,
    provider: FakeSandboxProvider,
    *,
    search_client: SearchClient | None = None,
    http_executor: HttpToolExecutor | None = None,
    **overrides: Any,
) -> TestClient:
    app = create_app(
        _settings(tmp_path, **overrides),
        verifier(),
        sandbox_provider=provider,
        search_client=search_client,
        http_executor=http_executor,
    )
    return TestClient(app)


def _execute(
    client: TestClient,
    arguments: dict[str, Any],
    *,
    tool: str = TOOL_NAME,
    tool_call_id: str = "call-ws",
) -> Any:
    return client.post(
        f"/v1/tools/{tool}:execute",
        json={"tool_call_id": tool_call_id, "arguments": arguments},
        headers=ACCOUNT,
    )


def _search_client(handler: Any) -> SearchClient:
    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    return SearchClient(
        provider="tavily",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        resolver=resolver,
    )


def test_tool_is_hidden_when_search_is_disabled(client: TestClient) -> None:
    names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
    assert TOOL_NAME not in names

    detail = client.get(f"/v1/tools/{TOOL_NAME}")
    assert detail.status_code == 404
    assert detail.json()["code"] == "tool.not_found"

    resp = _execute(client, {"query": "q"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.not_found"


def test_tool_is_visible_when_enabled_and_keyed(tmp_path: Path) -> None:
    with _custom_client(
        tmp_path,
        FakeSandboxProvider(),
        search_enabled=True,
        search_api_key="test-key",
    ) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
        assert TOOL_NAME in names
        # 目录顺序:web_search 排在 ask_question 之前(read 之后第八位)。
        assert names[-2] == TOOL_NAME

        detail = client.get(f"/v1/tools/{TOOL_NAME}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["risk"] == "read"
        assert body["executor"] == "http"
        assert body["input_schema"]["required"] == ["query"]


def test_tool_is_hidden_when_key_missing(tmp_path: Path) -> None:
    with _custom_client(tmp_path, FakeSandboxProvider(), search_enabled=True) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
        assert TOOL_NAME not in names
        assert client.get(f"/v1/tools/{TOOL_NAME}").status_code == 404


def test_tool_is_hidden_for_unknown_provider(tmp_path: Path) -> None:
    with _custom_client(
        tmp_path,
        FakeSandboxProvider(),
        search_enabled=True,
        search_api_key="test-key",
        search_provider="google",
    ) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
        assert TOOL_NAME not in names


def test_execute_returns_unavailable_without_executor(tmp_path: Path) -> None:
    # 开关与密钥齐备,但执行器没装搜索客户端:工具可见、执行回不可用。
    with _custom_client(
        tmp_path,
        FakeSandboxProvider(),
        http_executor=HttpToolExecutor(),
        search_enabled=True,
        search_api_key="test-key",
    ) as client:
        resp = _execute(client, {"query": "q"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.executor_unavailable"
    assert "TOOL__SEARCH_ENABLED" in body["error"]["hint"]


def test_execute_end_to_end_success(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Beauty Moon",
                        "url": "https://example.test/a",
                        "content": "hit",
                    }
                ]
            },
        )

    with _custom_client(
        tmp_path,
        FakeSandboxProvider(),
        search_client=_search_client(handler),
        search_enabled=True,
        search_api_key="test-key",
    ) as client:
        resp = _execute(client, {"query": "beauty moon", "max_results": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["provider"] == "tavily"
    assert body["results"][0]["rank"] == 1
    assert body["results"][0]["url"] == "https://example.test/a"
    assert captured["body"]["query"] == "beauty moon"
    assert captured["body"]["max_results"] == 2


def test_execute_validates_arguments(tmp_path: Path) -> None:
    with _custom_client(
        tmp_path,
        FakeSandboxProvider(),
        search_enabled=True,
        search_api_key="test-key",
    ) as client:
        resp = _execute(client, {"query": "", "max_results": 11})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.invalid_args"
