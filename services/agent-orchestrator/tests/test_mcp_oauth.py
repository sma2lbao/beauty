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
    payload = {"tenant_id": "t-1", "server_id": "s-1", "ts": time.time()}
    signed = mcp_oauth.sign_state(settings, payload)
    assert mcp_oauth.verify_state(settings, signed)["server_id"] == "s-1"

    with pytest.raises(AppError) as bad:
        mcp_oauth.verify_state(settings, signed[:-4] + "AAAA")
    assert bad.value.code == "mcp.oauth_state_invalid"

    expired = mcp_oauth.sign_state(settings, {**payload, "ts": time.time() - 3600})
    with pytest.raises(AppError) as stale:
        mcp_oauth.verify_state(settings, expired)
    assert stale.value.code == "mcp.oauth_state_expired"
