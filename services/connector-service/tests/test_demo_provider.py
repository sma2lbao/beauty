"""测试专用 demo provider 单测:错误映射、超时、限流与凭证解析(零网络)。"""

from __future__ import annotations

import httpx
import pytest

from connector.core.config import Settings
from connector.providers.base import Credentials
from connector.providers.errors import ProviderError
from tests.demo_provider import DemoProvider


def _settings(**overrides) -> Settings:
    values = {"_env_file": None, "env": "dev", "providers_enabled": "demo"}
    values.update(overrides)
    return Settings(**values)


def _provider(**overrides) -> DemoProvider:
    return DemoProvider(_settings(**overrides))


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


CREDS = Credentials(access_token="at-1", refresh_token="rt-1")


async def test_refresh_without_refresh_token() -> None:
    provider = _provider()
    async with _client(lambda request: httpx.Response(200, json={"code": 0})) as client:
        with pytest.raises(ProviderError) as excinfo:
            await provider.refresh(Credentials(access_token="at-1"), client)
    assert excinfo.value.code == "connector.auth_expired"


async def test_token_request_timeout() -> None:
    provider = _provider()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    async with _client(handler) as client:
        with pytest.raises(ProviderError) as excinfo:
            await provider.exchange_code(
                code="c", code_verifier="v", redirect_uri="r", client=client
            )
    assert excinfo.value.code == "connector.upstream_timeout"
    assert excinfo.value.retryable is True


async def test_token_request_network_error() -> None:
    provider = _provider()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with _client(handler) as client:
        with pytest.raises(ProviderError) as excinfo:
            await provider.exchange_code(
                code="c", code_verifier="v", redirect_uri="r", client=client
            )
    assert excinfo.value.code == "connector.upstream_unavailable"


async def test_token_request_without_access_token() -> None:
    provider = _provider()
    async with _client(lambda request: httpx.Response(200, json={"code": 0})) as client:
        with pytest.raises(ProviderError) as excinfo:
            await provider.exchange_code(
                code="c", code_verifier="v", redirect_uri="r", client=client
            )
    assert excinfo.value.code == "connector.oauth_failed"


async def test_tool_error_mappings() -> None:
    provider = _provider()
    cases = [
        (
            httpx.Response(429, json={"code": 0}),
            "connector.upstream_rate_limited",
            True,
        ),
        (
            httpx.Response(429, json={"code": 0}, headers={"retry-after": "5"}),
            "connector.upstream_rate_limited",
            True,
        ),
        (httpx.Response(403, json={"code": 0}), "connector.scope_missing", False),
        (httpx.Response(401, json={"code": 20005}), "connector.auth_expired", False),
        (httpx.Response(500, json={"code": 0}), "connector.upstream_unavailable", True),
        (httpx.Response(400, json={"code": 123}), "connector.upstream_error", False),
        (httpx.Response(200, json={"code": 20027}), "connector.scope_missing", False),
        (httpx.Response(200, text="not json"), "connector.upstream_error", False),
    ]
    for response, code, retryable in cases:
        async with _client(lambda request, r=response: r) as client:
            with pytest.raises(ProviderError) as excinfo:
                await provider.call("Demo__get_me", {}, CREDS.to_payload(), client)
        assert excinfo.value.code == code, (response.status_code, code)
        assert excinfo.value.retryable is retryable


async def test_tool_timeout_and_http_error() -> None:
    provider = _provider()

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with _client(timeout) as client:
        with pytest.raises(ProviderError) as excinfo:
            await provider.call("Demo__list_files", {}, CREDS.to_payload(), client)
    assert excinfo.value.code == "connector.upstream_timeout"

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async with _client(boom) as client:
        with pytest.raises(ProviderError) as excinfo:
            await provider.call("Demo__list_files", {}, CREDS.to_payload(), client)
    assert excinfo.value.code == "connector.upstream_unavailable"


async def test_invalid_arguments() -> None:
    provider = _provider()
    async with _client(lambda request: httpx.Response(200, json={"code": 0})) as client:
        with pytest.raises(ProviderError) as excinfo:
            await provider.call("Demo__read_document", {}, CREDS.to_payload(), client)
    assert excinfo.value.code == "connector.invalid_arguments"


def test_credentials_payload_roundtrip_and_validation() -> None:
    payload = CREDS.to_payload()
    assert Credentials.from_payload(payload).access_token == "at-1"
    with pytest.raises(ValueError, match="access_token"):
        Credentials.from_payload({"refresh_token": "x"})


def test_manifest_and_scopes() -> None:
    provider = _provider()
    assert provider.configured is True
    assert provider.scopes() == ("offline_access", "docs:readonly")
    assert provider.full_tool_name("get_me") == "Demo__get_me"
    assert len(provider.tools) == 6
    with pytest.raises(KeyError):
        provider.tool_spec("Demo__nope")
