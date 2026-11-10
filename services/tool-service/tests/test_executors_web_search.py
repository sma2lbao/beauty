"""web_search 执行器:提供方适配、截断、错误映射与出口边界(零网络)。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tool_service.services.executors.http import HttpToolExecutor
from tool_service.services.executors.web_search import (
    EMPTY_RESULTS_NOTE,
    SNIPPET_TRUNCATED_NOTE,
    TITLE_TRUNCATED_NOTE,
    SearchClient,
    SearchError,
)

PUBLIC_IP = "93.184.216.34"


def _resolver(mapping: dict[str, list[str]]):
    """按主机名返回固定地址;不在表里的主机按不可解析处理。"""

    async def resolve(host: str, port: int) -> list[str]:
        return mapping.get(host, [])

    return resolve


async def _public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def _client(
    handler: Any,
    *,
    provider: str = "tavily",
    resolver: Any = _public_resolver,
    base_url: str = "",
    **kwargs: Any,
) -> SearchClient:
    """构造注入了 MockTransport 与假解析器的搜索客户端。"""

    return SearchClient(
        provider=provider,
        api_key="test-key",
        base_url=base_url,
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        **kwargs,
    )


def _json_handler(payload: dict[str, Any], status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _respond(status: int, *, body: bytes = b""):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return handler


def _raise(error: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def _tavily_payload() -> dict[str, Any]:
    return {
        "results": [
            {
                "title": "Beauty Moon",
                "url": "https://example.test/a",
                "content": "first hit",
                "score": 0.9,
            },
            {
                "title": "Moon Docs",
                "url": "https://example.test/b",
                "content": "second hit",
                "score": 0.5,
            },
        ]
    }


async def test_tavily_request_and_envelope() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_tavily_payload())

    client = _client(handler)
    try:
        result = await client.search("beauty moon", max_results=2)
    finally:
        await client.aclose()

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["body"] == {
        "api_key": "test-key",
        "query": "beauty moon",
        "max_results": 2,
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
    }
    assert result["query"] == "beauty moon"
    assert result["provider"] == "tavily"
    assert result["results"] == [
        {
            "rank": 1,
            "title": "Beauty Moon",
            "url": "https://example.test/a",
            "snippet": "first hit",
        },
        {
            "rank": 2,
            "title": "Moon Docs",
            "url": "https://example.test/b",
            "snippet": "second hit",
        },
    ]
    assert "notes" not in result


async def test_brave_request_and_envelope() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["token"] = request.headers.get("X-Subscription-Token")
        captured["accept"] = request.headers.get("Accept")
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Brave Hit",
                            "url": "https://example.test/brave",
                            "description": "brave snippet",
                        }
                    ]
                }
            },
        )

    client = _client(handler, provider="brave")
    try:
        result = await client.search("beauty moon", max_results=3)
    finally:
        await client.aclose()

    assert captured["method"] == "GET"
    assert captured["path"] == "/res/v1/web/search"
    assert captured["params"] == {"q": "beauty moon", "count": "3"}
    assert captured["token"] == "test-key"
    assert captured["accept"] == "application/json"
    assert result["provider"] == "brave"
    assert result["results"][0]["snippet"] == "brave snippet"
    assert result["results"][0]["rank"] == 1


async def test_omitted_max_results_uses_default() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": []})

    client = _client(handler)
    try:
        await client.search("q")
    finally:
        await client.aclose()

    assert captured["body"]["max_results"] == 5


async def test_max_results_is_clamped_to_configured_limit() -> None:
    captured: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content)["max_results"])
        return httpx.Response(200, json={"results": []})

    client = _client(handler, max_results=10)
    try:
        await client.search("q", max_results=99)
    finally:
        await client.aclose()

    assert captured == [10]


async def test_default_results_is_clamped_to_max() -> None:
    captured: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content)["max_results"])
        return httpx.Response(200, json={"results": []})

    client = _client(handler, default_results=50, max_results=5)
    try:
        await client.search("q")
    finally:
        await client.aclose()

    assert captured == [5]


async def test_long_title_and_snippet_are_truncated_with_notes() -> None:
    payload = {
        "results": [
            {
                "title": "t" * 400,
                "url": "https://example.test/a",
                "content": "s" * 500,
            }
        ]
    }
    client = _client(_json_handler(payload))
    try:
        result = await client.search("q")
    finally:
        await client.aclose()

    hit = result["results"][0]
    assert len(hit["title"]) == 300
    assert hit["title"].endswith("…")
    assert len(hit["snippet"]) == 400
    assert hit["snippet"].endswith("…")
    assert result["notes"] == [TITLE_TRUNCATED_NOTE, SNIPPET_TRUNCATED_NOTE]


async def test_empty_results_is_not_an_error() -> None:
    client = _client(_json_handler({"results": []}))
    try:
        result = await client.search("q")
    finally:
        await client.aclose()

    assert result["results"] == []
    assert result["notes"] == [EMPTY_RESULTS_NOTE]


async def test_invalid_urls_are_dropped_and_ranks_stay_contiguous() -> None:
    payload = {
        "results": [
            {"title": "no url", "url": "", "content": "x"},
            {"title": "too long", "url": "https://example.test/" + "x" * 3000},
            {"title": "ok", "url": "https://example.test/ok", "content": "y"},
        ]
    }
    client = _client(_json_handler(payload))
    try:
        result = await client.search("q")
    finally:
        await client.aclose()

    assert [hit["url"] for hit in result["results"]] == ["https://example.test/ok"]
    assert result["results"][0]["rank"] == 1


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_is_not_retryable(status: int) -> None:
    client = _client(_respond(status))
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.search_auth_failed"
    assert excinfo.value.retryable is False
    assert "TOOL__SEARCH_API_KEY" in excinfo.value.hint


async def test_rate_limit_is_retryable() -> None:
    client = _client(_respond(429))
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_rate_limited"
    assert excinfo.value.retryable is True


async def test_server_error_is_retryable_and_hint_is_body() -> None:
    client = _client(_respond(500, body=b"provider boom"))
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_unavailable"
    assert excinfo.value.retryable is True
    assert "provider boom" in excinfo.value.hint


async def test_redirect_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"Location": "https://evil.test/steal"}, content=b""
        )

    client = _client(handler)
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_bad_response"
    assert excinfo.value.retryable is False
    assert "302" in excinfo.value.hint


async def test_timeout_maps_to_timeout() -> None:
    client = _client(_raise(httpx.ReadTimeout("slow")), timeout_seconds=3.0)
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.timeout"
    assert excinfo.value.retryable is True
    assert "3 秒" in excinfo.value.hint


async def test_network_error_maps_to_upstream_unavailable() -> None:
    client = _client(_raise(httpx.ConnectError("no route")))
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_unavailable"
    assert excinfo.value.retryable is True


@pytest.mark.parametrize(
    "body",
    [b"not json", b"", b"\xff\xfe"],
)
async def test_bad_body_maps_to_upstream_bad_response(body: bytes) -> None:
    client = _client(_respond(200, body=body))
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_bad_response"
    assert excinfo.value.retryable is True


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("tavily", {"results": "oops"}),
        ("brave", {"web": "oops"}),
        ("brave", {"web": {"results": "oops"}}),
    ],
)
async def test_structurally_wrong_response_is_bad_response(
    provider: str, payload: dict[str, Any]
) -> None:
    client = _client(_json_handler(payload), provider=provider)
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_bad_response"


async def test_oversized_body_is_abandoned() -> None:
    body = json.dumps(_tavily_payload()).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    client = _client(handler, max_bytes=64)
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_bad_response"
    assert "64" in excinfo.value.hint


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.5",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
    ],
)
async def test_private_addresses_are_blocked(address: str) -> None:
    client = _client(
        _json_handler({"results": []}),
        resolver=_resolver({"api.tavily.com": [address]}),
    )
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    error = excinfo.value
    assert error.code == "tool.search_blocked"
    assert error.retryable is False
    assert address not in error.message
    assert address not in error.hint


def _dns_error_resolver(host: str, port: int):
    async def resolve(host: str, port: int) -> list[str]:
        raise OSError("dns down")

    return resolve


@pytest.mark.parametrize(
    "resolver",
    [
        _resolver({}),
        _dns_error_resolver("", 0),
    ],
)
async def test_unresolvable_provider_is_blocked(resolver: Any) -> None:
    client = _client(_json_handler({"results": []}), resolver=resolver)
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.search_blocked"
    assert excinfo.value.retryable is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": ""},
        {"provider": "google"},
        {"base_url": "http://api.tavily.com"},
        {"base_url": "https://"},
    ],
)
def test_constructor_rejects_invalid_configuration(kwargs: dict[str, Any]) -> None:
    merged = {"api_key": "test-key", **kwargs}
    with pytest.raises(ValueError):
        SearchClient(transport=httpx.MockTransport(_respond(200)), **merged)


async def test_custom_https_base_url_is_used() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["path"] = request.url.path
        return httpx.Response(200, json={"results": []})

    client = _client(handler, base_url="https://search.example.test/")
    try:
        await client.search("q")
    finally:
        await client.aclose()

    assert captured == {"host": "search.example.test", "path": "/search"}


# ---- 执行器分派与错误信封 ------------------------------------------------


async def test_executor_reports_missing_client() -> None:
    response = await HttpToolExecutor().execute("t-1", "web_search", {"query": "q"})

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.executor_unavailable"
    assert "TOOL__SEARCH_ENABLED" in response.error.hint


async def test_executor_returns_success_envelope() -> None:
    client = _client(_json_handler(_tavily_payload()))
    try:
        response = await HttpToolExecutor(searcher=client).execute(
            "t-1", "web_search", {"query": "q", "max_results": 1}
        )
    finally:
        await client.aclose()

    assert response.ok is True
    assert response.result["provider"] == "tavily"
    assert response.result["results"][0]["rank"] == 1


async def test_executor_maps_search_error() -> None:
    client = _client(_respond(429))
    try:
        response = await HttpToolExecutor(searcher=client).execute(
            "t-1", "web_search", {"query": "q"}
        )
    finally:
        await client.aclose()

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.upstream_rate_limited"
    assert response.error.retryable is True


class _HugeSearcher:
    """结果远超单条工具上限的替身(验证 too_large 保险丝)。"""

    async def search(
        self, query: str, *, max_results: int | None = None
    ) -> dict[str, Any]:
        return {
            "query": query,
            "provider": "tavily",
            "results": [
                {
                    "rank": 1,
                    "title": "t" * 25_000,
                    "url": "https://example.test/a",
                    "snippet": "",
                }
            ],
        }


async def test_executor_rejects_oversized_result() -> None:
    response = await HttpToolExecutor(searcher=_HugeSearcher()).execute(
        "t-1", "web_search", {"query": "q"}
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.too_large"
    assert response.error.retryable is True
