"""web_search 执行器:错误映射、超时与出口边界/SSRF 拦截(零网络)。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.web_search_support import (
    json_handler,
    raise_error,
    respond,
    search_client,
    tavily_payload,
)
from tests.web_search_support import resolver as make_resolver
from tool_service.services.executors.web_search import SearchError


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_is_not_retryable(status: int) -> None:
    client = search_client(respond(status))
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.search_auth_failed"
    assert excinfo.value.retryable is False
    assert "TOOL__SEARCH_API_KEY" in excinfo.value.hint


async def test_rate_limit_is_retryable() -> None:
    client = search_client(respond(429))
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_rate_limited"
    assert excinfo.value.retryable is True


async def test_server_error_is_retryable_and_hint_is_body() -> None:
    client = search_client(respond(500, body=b"provider boom"))
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

    client = search_client(handler)
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_bad_response"
    assert excinfo.value.retryable is False
    assert "302" in excinfo.value.hint


async def test_timeout_maps_to_timeout() -> None:
    client = search_client(raise_error(httpx.ReadTimeout("slow")), timeout_seconds=3.0)
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.timeout"
    assert excinfo.value.retryable is True
    assert "3 秒" in excinfo.value.hint


async def test_network_error_maps_to_upstream_unavailable() -> None:
    client = search_client(raise_error(httpx.ConnectError("no route")))
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
    client = search_client(respond(200, body=body))
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
    client = search_client(json_handler(payload), provider=provider)
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.upstream_bad_response"


async def test_oversized_body_is_abandoned() -> None:
    body = json.dumps(tavily_payload()).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    client = search_client(handler, max_bytes=64)
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
    client = search_client(
        json_handler({"results": []}),
        resolver=make_resolver({"api.tavily.com": [address]}),
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
        make_resolver({}),
        _dns_error_resolver("", 0),
    ],
)
async def test_unresolvable_provider_is_blocked(resolver: Any) -> None:
    client = search_client(json_handler({"results": []}), resolver=resolver)
    try:
        with pytest.raises(SearchError) as excinfo:
            await client.search("q")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.search_blocked"
    assert excinfo.value.retryable is False
