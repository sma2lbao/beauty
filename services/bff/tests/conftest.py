"""BFF 测试装配:MockTransport 模拟 account/runtime/workspace,零网络。"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from bff.core.config import Settings
from bff.main import create_app

ACCESS = "access-1"
REFRESH = "refresh-1"


def token_payload(access: str = ACCESS, refresh: str = REFRESH) -> dict:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": 3600,
        "tenant_id": "t1",
        "organization_id": "t1",
        "user_id": "u1",
        "subject": "user@example.com",
        "role": "owner",
        "session_id": "s1",
    }


def settings(**overrides) -> Settings:
    values = {
        "env": "dev",
        "session_secret": "test-session-secret",
        "account_base_url": "http://account",
        "runtime_base_url": "http://runtime",
        "workspace_base_url": "http://workspace",
        "knowledge_base_url": "http://knowledge",
        "internal_secret": "internal-secret",
        "rate_limit_per_minute": 1000,
    }
    values.update(overrides)
    return Settings(**values)


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/auth/login":
        return httpx.Response(200, json=token_payload())
    if path == "/v1/auth/register":
        return httpx.Response(201, json=token_payload())
    if path == "/v1/auth/refresh":
        return httpx.Response(200, json=token_payload("access-2", "refresh-2"))
    if path == "/v1/auth/logout":
        return httpx.Response(204)
    if path == "/v1/auth/oauth/google/authorize":
        return httpx.Response(
            200,
            json={
                "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?x=1",
                "state": "signed-state",
            },
        )
    if path == "/v1/auth/oauth/google/exchange":
        return httpx.Response(200, json=token_payload())
    if path == "/v1/auth/issue":
        return httpx.Response(
            200,
            json={"access_token": "stream-token", "expires_in": 300},
        )
    if path == "/v1/artifacts/a1":
        return httpx.Response(
            200,
            json={
                "id": "a1",
                "title": "周报",
                "kind": "markdown",
                "content": "# 周报",
            },
        )
    if path == "/api/v1/runs":
        auth = request.headers.get("authorization")
        if auth != f"Bearer {ACCESS}":
            return httpx.Response(401, json={"code": "auth.expired", "message": "过期"})
        return httpx.Response(200, json=[{"id": "r1", "status": "completed"}])
    if path == "/api/v1/runs/expired":
        auth = request.headers.get("authorization")
        if auth == f"Bearer {ACCESS}":
            return httpx.Response(401, json={"code": "auth.expired", "message": "过期"})
        return httpx.Response(200, json={"id": "r1"})
    if path == "/api/v1/runs/r1/events/stream":
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"event: stream.end\ndata: {}\n\n",
        )
    return httpx.Response(404, json={"code": "not_found", "message": "not found"})


@pytest.fixture()
def client(request) -> Iterator[TestClient]:
    overrides = getattr(request, "param", {}) or {}
    app = create_app(settings(**overrides), transport=httpx.MockTransport(handler))
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    csrf = client.cookies.get("bm_csrf")
    assert csrf
    return csrf
