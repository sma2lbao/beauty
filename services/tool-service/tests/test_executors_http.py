"""HTTP 执行器:知识库检索代理的请求/响应/错误映射(httpx.MockTransport,零网络)。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from common.errors import UpstreamError

from tool_service.services.executors.http import (
    HttpToolExecutor,
    KnowledgeClient,
    UpstreamStatusError,
)


async def _token(tenant_id: str) -> str:
    return f"token-{tenant_id}"


def _client(handler: Any, *, token_provider: Any = _token) -> KnowledgeClient:
    return KnowledgeClient(
        "http://knowledge.test",
        timeout_seconds=3.0,
        transport=httpx.MockTransport(handler),
        token_provider=token_provider,
    )


def _executor(handler: Any) -> HttpToolExecutor:
    return HttpToolExecutor(knowledge=_client(handler))


async def test_knowledge_client_posts_expected_request() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "query": "部署",
                "provider": "fake",
                "model": "fake",
                "hits": [{"title": "T", "score": 0.5, "content": "C", "chunk_id": "x"}],
            },
        )

    client = _client(handler)
    try:
        result = await client.search("t-1", query="部署", limit=3)
    finally:
        await client.aclose()

    request = captured[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/knowledge/search"
    assert request.headers["authorization"] == "Bearer token-t-1"
    assert request.content
    assert httpx.Response(200, content=request.content).json() == {
        "query": "部署",
        "limit": 3,
    }
    # 归一形状与旧 orchestrator 工具一致,chunk_id 等内部字段不进模型结果。
    assert result == {
        "query": "部署",
        "provider": "fake",
        "model": "fake",
        "hits": [{"title": "T", "score": 0.5, "content": "C"}],
    }


@pytest.mark.parametrize("status", [404, 422, 500])
async def test_knowledge_client_raises_status_error(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="boom")

    client = _client(handler)
    try:
        with pytest.raises(UpstreamStatusError) as raised:
            await client.search("t-1", query="q", limit=5)
    finally:
        await client.aclose()

    assert raised.value.status_code == status
    assert raised.value.body == "boom"


async def test_knowledge_client_rejects_non_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    client = _client(handler)
    try:
        with pytest.raises(UpstreamError) as raised:
            await client.search("t-1", query="q", limit=5)
    finally:
        await client.aclose()

    assert raised.value.code == "knowledge.bad_response"


async def test_knowledge_client_rejects_non_object_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    client = _client(handler)
    try:
        with pytest.raises(UpstreamError) as raised:
            await client.search("t-1", query="q", limit=5)
    finally:
        await client.aclose()

    assert raised.value.code == "knowledge.bad_response"


async def test_executor_search_knowledge_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "query": "q",
                "provider": "fake",
                "model": "fake",
                "hits": [{"title": "T", "score": 1, "content": "C"}],
            },
        )

    response = await _executor(handler).execute(
        "t-1", "search_knowledge", {"query": "q"}
    )

    assert response.ok is True
    assert response.error is None
    assert response.result is not None
    assert response.result["hits"] == [{"title": "T", "score": 1, "content": "C"}]


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (404, "tool.not_found", False),
        (400, "tool.invalid_args", False),
        (500, "tool.upstream_unavailable", True),
        (503, "tool.upstream_unavailable", True),
    ],
)
async def test_executor_maps_status_codes(
    status: int, code: str, retryable: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="detail")

    response = await _executor(handler).execute(
        "t-1", "search_knowledge", {"query": "q"}
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == code
    assert response.error.retryable is retryable


async def test_executor_maps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    response = await _executor(handler).execute(
        "t-1", "search_knowledge", {"query": "q"}
    )

    assert response.error is not None
    assert response.error.code == "tool.timeout"
    assert response.error.retryable is True


async def test_executor_maps_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    response = await _executor(handler).execute(
        "t-1", "search_knowledge", {"query": "q"}
    )

    assert response.error is not None
    assert response.error.code == "tool.upstream_unavailable"
    assert response.error.retryable is True


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (UpstreamError("auth.internal_secret_missing", "no secret"), False),
        (UpstreamError("auth.token_broker_error", "idp down"), True),
    ],
)
async def test_executor_maps_token_broker_errors(
    error: Exception, retryable: bool
) -> None:
    async def failing_token(tenant_id: str) -> str:
        raise error

    client = _client(
        lambda request: httpx.Response(200, json={}), token_provider=failing_token
    )
    response = await HttpToolExecutor(knowledge=client).execute(
        "t-1", "search_knowledge", {"query": "q"}
    )
    await client.aclose()

    assert response.error is not None
    assert response.error.code == "tool.upstream_unavailable"
    assert response.error.retryable is retryable


async def test_executor_rejects_unknown_http_tool() -> None:
    response = await _executor(lambda request: httpx.Response(200, json={})).execute(
        "t-1", "write", {"path": "a.txt"}
    )

    assert response.error is not None
    assert response.error.code == "tool.executor_unavailable"


async def test_executor_requires_knowledge_client() -> None:
    response = await HttpToolExecutor().execute(
        "t-1", "search_knowledge", {"query": "q"}
    )

    assert response.error is not None
    assert response.error.code == "tool.executor_unavailable"
