"""MCP OAuth 发现、动态注册与 state 签名。"""

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from common.errors import AppError, UpstreamError

from agent_orchestrator.services import mcp_oauth
from tests.support.mcp_oauth import (
    AUTHORIZE_ENDPOINT,
    ISSUER,
    PRM,
    REDIRECT_URI,
    RESOURCE_URL,
    TOKEN_ENDPOINT,
    FakeAuthServer,
    FakeMissingMetadata,
    _settings,
)

# ---------------------------------------------------------------- 发现 / 注册


def test_discovery_uses_www_authenticate_hint() -> None:
    server = FakeAuthServer()
    hint = (
        f'Bearer resource_metadata="{RESOURCE_URL}'
        '/.well-known/oauth-protected-resource"'
    )

    async def scenario() -> dict[str, Any]:
        return await mcp_oauth.discover_protected_resource(
            RESOURCE_URL, hint=hint, transport=server.transport, timeout=2
        )

    assert asyncio.run(scenario())["authorization_servers"] == [ISSUER]
    # 401 原文里的 resource_metadata 优先,不再猜 origin 级候选
    assert server.seen[0] == (
        "http://resource.local/.well-known/oauth-protected-resource/mcp"
    )


def test_discovery_falls_back_to_origin_well_known() -> None:
    server = FakeAuthServer()

    async def scenario() -> dict[str, Any]:
        return await mcp_oauth.discover_protected_resource(
            "http://resource.local", transport=server.transport, timeout=2
        )

    assert asyncio.run(scenario())["resource"] == RESOURCE_URL
    assert server.seen[0] == (
        "http://resource.local/.well-known/oauth-protected-resource"
    )


def test_discovery_failure_reports_rfc9728_missing() -> None:
    server = FakeMissingMetadata()

    async def scenario() -> None:
        await mcp_oauth.discover_protected_resource(
            RESOURCE_URL, transport=server.transport, timeout=2
        )

    with pytest.raises(UpstreamError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_discovery_failed"
    details = err.value.details
    assert details["stage"] == "resource_metadata"
    assert details["reason"] == "no_metadata"
    # 可达但没公布元数据 → 指引改用静态令牌,而不是让用户去查网络
    assert details["action"] == "use_static_token"
    assert {item["outcome"] for item in details["tried"]} == {"http"}


def _unreachable_transport() -> httpx.MockTransport:
    """任何请求都连不上(DNS 解析失败/连接被拒)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "dial tcp: connect: connection refused", request=request
        )

    return httpx.MockTransport(handler)


def test_discovery_unreachable_reports_network_cause() -> None:
    async def scenario() -> None:
        await mcp_oauth.discover_protected_resource(
            RESOURCE_URL, transport=_unreachable_transport(), timeout=2
        )

    with pytest.raises(UpstreamError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_discovery_unreachable"
    details = err.value.details
    assert details["stage"] == "resource_metadata"
    assert details["reason"] == "network"
    # 网络问题必须指到"查网络",不能和"改用令牌"混成一个错误
    assert details["action"] == "check_network"
    assert details["resource"] == RESOURCE_URL
    assert details["tried"]
    assert all(item["outcome"] == "network" for item in details["tried"])
    assert "connection refused" in err.value.message


def test_discovery_invalid_metadata_reports_invalid() -> None:
    """200 JSON 但缺 authorization_servers:元数据写错,不是网络问题。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth-protected-resource" in str(request.url):
            return httpx.Response(200, json={"resource": RESOURCE_URL})
        return httpx.Response(404)

    async def scenario() -> None:
        await mcp_oauth.discover_protected_resource(
            RESOURCE_URL, transport=httpx.MockTransport(handler), timeout=2
        )

    with pytest.raises(UpstreamError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_discovery_invalid"
    assert err.value.details["reason"] == "missing_authorization_servers"
    assert err.value.details["action"] == "use_static_token"


PRM_URL = "http://auth.local/prm.json"


class _ChallengeResource:
    """资源端点回 401,WWW-Authenticate 指向非 origin 的元数据地址。"""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.seen.append((request.method, url))
        if request.method == "POST" and url == RESOURCE_URL:
            return httpx.Response(
                401,
                headers={"WWW-Authenticate": f'Bearer resource_metadata="{PRM_URL}"'},
            )
        if request.method == "GET" and url == PRM_URL:
            return httpx.Response(200, json=PRM)
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def test_discovery_probes_401_challenge_when_hint_missing() -> None:
    """预设一键连接没有历史 401 文本:主动探测拿权威 resource_metadata。"""
    resource = _ChallengeResource()

    async def scenario() -> dict[str, Any]:
        return await mcp_oauth.discover_protected_resource(
            RESOURCE_URL, transport=resource.transport, timeout=2
        )

    assert asyncio.run(scenario())["authorization_servers"] == [ISSUER]
    assert ("POST", RESOURCE_URL) in resource.seen


def _unreachable_authorization_server() -> httpx.MockTransport:
    """资源元数据正常,但授权服务器连不上。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth-protected-resource" in str(request.url):
            return httpx.Response(200, json=PRM)
        raise httpx.ConnectError("as: connection refused", request=request)

    return httpx.MockTransport(handler)


def test_authorization_server_unreachable_is_distinguished() -> None:
    async def scenario() -> None:
        await mcp_oauth.discover_authorization_server(
            ISSUER, transport=_unreachable_authorization_server(), timeout=2
        )

    with pytest.raises(UpstreamError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_authorization_server_unreachable"
    details = err.value.details
    assert details["stage"] == "authorization_server"
    assert details["action"] == "check_network"
    assert details["issuer"] == ISSUER


def test_authorization_server_metadata_missing_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth-authorization-server" in str(request.url):
            return httpx.Response(200, json={"issuer": ISSUER})
        return httpx.Response(404)

    async def scenario() -> None:
        await mcp_oauth.discover_authorization_server(
            ISSUER, transport=httpx.MockTransport(handler), timeout=2
        )

    with pytest.raises(UpstreamError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_authorization_server_invalid"
    assert err.value.details["reason"] == "missing_endpoints"
    assert err.value.details["action"] == "use_static_token"


def test_authorization_server_without_metadata_is_distinguished() -> None:
    async def scenario() -> None:
        await mcp_oauth.discover_authorization_server(
            ISSUER,
            transport=httpx.MockTransport(lambda request: httpx.Response(404)),
            timeout=2,
        )

    with pytest.raises(UpstreamError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_authorization_server_invalid"
    assert err.value.details["reason"] == "no_metadata"


def test_authorization_server_discovery_requires_endpoints() -> None:
    server = FakeAuthServer()

    async def scenario() -> dict[str, Any]:
        return await mcp_oauth.discover_authorization_server(
            ISSUER, transport=server.transport, timeout=2
        )

    metadata = asyncio.run(scenario())
    assert metadata["token_endpoint"] == TOKEN_ENDPOINT
    assert metadata["authorization_endpoint"] == AUTHORIZE_ENDPOINT


def test_registration_unsupported_without_registration_endpoint() -> None:
    metadata = {
        "authorization_endpoint": AUTHORIZE_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
    }

    async def scenario() -> None:
        await mcp_oauth.register_client(
            metadata,
            REDIRECT_URI,
            client_name="beauty-moon agent",
            transport=httpx.MockTransport(lambda request: httpx.Response(404)),
            timeout=2,
        )

    with pytest.raises(UpstreamError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_registration_unsupported"


# ----------------------------------------------------------------------- state


def test_state_roundtrip_tamper_and_expiry(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    payload = {"account_id": "t-1", "server_id": "s-1", "ts": time.time()}
    signed = mcp_oauth.sign_state(settings, payload)
    assert mcp_oauth.verify_state(settings, signed)["server_id"] == "s-1"

    with pytest.raises(AppError) as bad:
        mcp_oauth.verify_state(settings, signed[:-4] + "AAAA")
    assert bad.value.code == "mcp.oauth_state_invalid"

    expired = mcp_oauth.sign_state(settings, {**payload, "ts": time.time() - 3600})
    with pytest.raises(AppError) as stale:
        mcp_oauth.verify_state(settings, expired)
    assert stale.value.code == "mcp.oauth_state_expired"
