"""服务间 token(§5):TokenBroker 换发/缓存,下游客户端注入 Bearer。"""

from __future__ import annotations

import json

import httpx
import pytest
from common.errors import UpstreamError

from agent_orchestrator.services.connectors import ConnectorClient
from agent_orchestrator.services.token_broker import TokenBroker

ISSUE_BODY = {"access_token": "tok-1", "expires_in": 3600, "tenant_id": "t-a"}


async def test_token_issued_with_secret_header() -> None:
    """换发:带 X-Internal-Secret 与目标租户;返回 token。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ISSUE_BODY)

    broker = TokenBroker(
        base_url="http://account.local",
        internal_secret="s3cret",
        transport=httpx.MockTransport(handler),
    )
    assert await broker.token("t-a") == "tok-1"
    assert seen[0].headers["X-Internal-Secret"] == "s3cret"
    assert json.loads(seen[0].read())["tenant_id"] == "t-a"
    await broker.aclose()


async def test_token_cached_until_expiry() -> None:
    """缓存:未过期前同租户复用,零重复请求。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=ISSUE_BODY)

    broker = TokenBroker(
        base_url="http://account.local",
        internal_secret="s3cret",
        transport=httpx.MockTransport(handler),
    )
    assert await broker.token("t-a") == "tok-1"
    assert await broker.token("t-a") == "tok-1"
    assert calls["n"] == 1
    await broker.aclose()


async def test_broker_maps_upstream_error() -> None:
    """签发端点拒绝(401)→ UpstreamError,不向调用方泄漏响应体。"""
    broker = TokenBroker(
        base_url="http://account.local",
        internal_secret="wrong",
        transport=httpx.MockTransport(lambda _: httpx.Response(401)),
    )
    with pytest.raises(UpstreamError):
        await broker.token("t-a")
    await broker.aclose()


async def test_connector_client_injects_bearer() -> None:
    """注入:装配 token_provider 后,下游请求同时带租户头与 Bearer。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    client = ConnectorClient(
        "http://connector.local",
        transport=httpx.MockTransport(handler),
        token_provider=_stub_token,
    )
    await client.list_connections("t-a")
    assert seen[0].headers["X-Tenant-Id"] == "t-a"
    assert seen[0].headers["Authorization"] == "Bearer tok-for-t-a"
    await client.aclose()


async def _stub_token(tenant: str) -> str:
    return f"tok-for-{tenant}"
