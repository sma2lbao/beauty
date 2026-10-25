"""fetch 执行器:SSRF 边界、重定向、大小上限与错误映射(httpx.MockTransport,零网络)。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tool_service.services.executors.fetch import (
    ALLOWED_SCHEMES,
    DEFAULT_FETCH_MAX_BYTES,
    MAX_FETCH_TEXT_CHARS,
    FetchClient,
    FetchError,
    is_public_address,
    parse_target,
)
from tool_service.services.executors.http import HttpToolExecutor

PUBLIC_IP = "93.184.216.34"


def _resolver(mapping: dict[str, list[str]]):
    """按主机名返回固定地址;不在表里的主机按不可解析处理。"""

    async def resolve(host: str, port: int) -> list[str]:
        return mapping.get(host, [])

    return resolve


async def _public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def _text_client(
    handler: Any,
    *,
    resolver: Any = _public_resolver,
    **kwargs: Any,
) -> FetchClient:
    """构造注入了 MockTransport 与假解析器的抓取客户端。"""

    return FetchClient(
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        **kwargs,
    )


def _respond(status: int, *, body: bytes = b"", headers: dict[str, str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers=headers or {})

    return handler


def _raise(error: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def test_parse_target_keeps_scheme_host_port() -> None:
    assert parse_target("https://api.github.com/repos/x/y") == (
        "https",
        "api.github.com",
        443,
    )
    assert parse_target("http://example.com:8080/a?b=1") == (
        "http",
        "example.com",
        8080,
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "https://user:pass@example.com/",
        "https:///no-host",
        "http://example.com:bad/",
        "not a url",
    ],
)
def test_parse_target_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(FetchError) as excinfo:
        parse_target(url)
    assert excinfo.value.code == "tool.invalid_args"


def test_allowed_schemes_are_http_only() -> None:
    assert ALLOWED_SCHEMES == ("http", "https")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (PUBLIC_IP, True),
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.3.9", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:10.1.2.3", False),
        ("not-an-address", False),
    ],
)
def test_is_public_address(raw: str, expected: bool) -> None:
    assert is_public_address(raw) is expected


async def test_get_returns_text_and_metadata() -> None:
    client = _text_client(
        _respond(200, body=b"hello world", headers={"content-type": "text/plain"})
    )
    try:
        result = await client.get("https://example.com/hello")
    finally:
        await client.aclose()

    assert result["url"] == "https://example.com/hello"
    assert result["final_url"] == "https://example.com/hello"
    assert result["status"] == 200
    assert result["content_type"] == "text/plain"
    assert result["bytes"] == len(b"hello world")
    assert result["truncated"] is False
    assert result["text"] == "hello world"


async def test_get_decodes_declared_charset() -> None:
    client = _text_client(
        _respond(
            200,
            body="你好".encode("gbk"),
            headers={"content-type": "text/html; charset=gbk"},
        )
    )
    try:
        result = await client.get("https://example.com/gbk")
    finally:
        await client.aclose()

    assert result["text"] == "你好"


async def test_get_returns_json_as_text() -> None:
    body = b'{"name": "ai-infra-book"}'
    client = _text_client(
        _respond(200, body=body, headers={"content-type": "application/json"})
    )
    try:
        result = await client.get("https://api.github.com/repos/x/y")
    finally:
        await client.aclose()

    assert result["status"] == 200
    assert '"name"' in result["text"]


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"],
)
async def test_get_blocks_non_public_addresses(address: str) -> None:
    client = _text_client(
        _respond(200, body=b"secret"),
        resolver=_resolver({"example.com": [address]}),
    )
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://example.com/")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.fetch_blocked"


async def test_get_blocks_when_any_resolved_address_is_private() -> None:
    client = _text_client(
        _respond(200, body=b"ok"),
        resolver=_resolver({"example.com": [PUBLIC_IP, "10.0.0.5"]}),
    )
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://example.com/")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.fetch_blocked"


async def test_get_blocks_unresolvable_host_as_retryable() -> None:
    client = _text_client(_respond(200, body=b"ok"), resolver=_resolver({}))
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://missing.test/")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.fetch_blocked"
    assert excinfo.value.retryable is True


async def test_allowed_hosts_restricts_exit() -> None:
    client = _text_client(
        _respond(200, body=b"ok"),
        allowed_hosts=("example.com",),
    )
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://other.test/")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.fetch_blocked"
    assert "example.com" in excinfo.value.hint


async def test_allowed_hosts_accepts_subdomains() -> None:
    client = _text_client(
        _respond(200, body=b"ok"),
        allowed_hosts=("example.com",),
    )
    try:
        result = await client.get("https://sub.example.com/x")
    finally:
        await client.aclose()
    assert result["status"] == 200


async def test_redirect_to_private_address_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.test/secret"})

    client = _text_client(
        handler,
        resolver=_resolver({"example.com": [PUBLIC_IP], "internal.test": ["10.0.0.5"]}),
    )
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://example.com/start")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.fetch_blocked"


async def test_public_redirect_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(
            200, content=b"landed", headers={"content-type": "text/plain"}
        )

    client = _text_client(handler)
    try:
        result = await client.get("https://example.com/start")
    finally:
        await client.aclose()

    assert result["final_url"] == "https://example.com/final"
    assert result["text"] == "landed"


async def test_too_many_redirects_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/loop"})

    client = _text_client(handler, max_redirects=2)
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://example.com/loop")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.too_many_redirects"


async def test_404_maps_to_not_found() -> None:
    client = _text_client(_respond(404, body=b"missing"))
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://example.com/nope")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.not_found"


async def test_4xx_maps_to_fetch_failed() -> None:
    client = _text_client(_respond(403, body=b"forbidden"))
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://example.com/secret")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.fetch_failed"
    assert excinfo.value.retryable is False


async def test_5xx_maps_to_retryable_fetch_failed() -> None:
    client = _text_client(_respond(503, body=b"down"))
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://example.com/down")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.fetch_failed"
    assert excinfo.value.retryable is True


async def test_binary_content_is_rejected() -> None:
    client = _text_client(
        _respond(200, body=b"\x89PNG\r\n", headers={"content-type": "image/png"})
    )
    try:
        with pytest.raises(FetchError) as excinfo:
            await client.get("https://example.com/logo.png")
    finally:
        await client.aclose()

    assert excinfo.value.code == "tool.unsupported_content"


async def test_body_is_truncated_at_byte_limit() -> None:
    client = _text_client(
        _respond(200, body=b"x" * 100, headers={"content-type": "text/plain"}),
        max_bytes=10,
    )
    try:
        result = await client.get("https://example.com/big")
    finally:
        await client.aclose()

    assert result["bytes"] == 10
    assert result["truncated"] is True


async def test_long_text_is_truncated_at_char_limit() -> None:
    client = _text_client(
        _respond(
            200,
            body=b"y" * (MAX_FETCH_TEXT_CHARS + 500),
            headers={"content-type": "text/plain"},
        )
    )
    try:
        result = await client.get("https://example.com/huge")
    finally:
        await client.aclose()

    assert result["truncated"] is True
    assert len(result["text"]) == MAX_FETCH_TEXT_CHARS


async def test_default_byte_limit_is_one_megabyte() -> None:
    assert DEFAULT_FETCH_MAX_BYTES == 1024 * 1024


async def test_executor_reports_unavailable_without_fetcher() -> None:
    executor = HttpToolExecutor()
    response = await executor.execute("t-1", "fetch", {"url": "https://example.com/"})

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.executor_unavailable"
    assert "fetch" in response.error.message


async def test_executor_returns_fetch_result() -> None:
    fetcher = _text_client(
        _respond(200, body=b"content", headers={"content-type": "text/plain"})
    )
    executor = HttpToolExecutor(fetcher=fetcher)
    try:
        response = await executor.execute(
            "t-1", "fetch", {"url": "https://example.com/page"}
        )
    finally:
        await fetcher.aclose()

    assert response.ok is True
    assert response.result["text"] == "content"
    assert response.result["final_url"] == "https://example.com/page"


async def test_executor_maps_fetch_error_to_envelope() -> None:
    fetcher = _text_client(
        _respond(200, body=b"secret"),
        resolver=_resolver({"example.com": ["127.0.0.1"]}),
    )
    executor = HttpToolExecutor(fetcher=fetcher)
    try:
        response = await executor.execute(
            "t-1", "fetch", {"url": "https://example.com/"}
        )
    finally:
        await fetcher.aclose()

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.fetch_blocked"
    assert response.error.retryable is False


async def test_executor_maps_timeout_to_retryable_error() -> None:
    fetcher = _text_client(_raise(httpx.ConnectTimeout("timed out")))
    executor = HttpToolExecutor(fetcher=fetcher)
    try:
        response = await executor.execute(
            "t-1", "fetch", {"url": "https://example.com/"}
        )
    finally:
        await fetcher.aclose()

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.timeout"
    assert response.error.retryable is True


async def test_executor_maps_http_error_to_retryable_error() -> None:
    fetcher = _text_client(_raise(httpx.ConnectError("connection refused")))
    executor = HttpToolExecutor(fetcher=fetcher)
    try:
        response = await executor.execute(
            "t-1", "fetch", {"url": "https://example.com/"}
        )
    finally:
        await fetcher.aclose()

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.fetch_failed"
    assert response.error.retryable is True
