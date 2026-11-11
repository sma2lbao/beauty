"""API 网关测试装配:MockTransport 模拟 account/runtime/workspace,零网络。"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import structlog
from common.service_testkit import build_settings
from fastapi.testclient import TestClient

from api_gateway.api import proxy as proxy_module
from api_gateway.core.config import Settings
from api_gateway.main import create_app

ACCESS = "access-1"
REFRESH = "refresh-1"


@pytest.fixture(autouse=True)
def _fresh_proxy_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """structlog 首次使用即缓存绑定(common.logging 开启 cache_logger_on_first_use)。

    模块级 logger 若在上一测试的 capture_logs 内完成绑定,后续测试的 proxy_call
    会写进已关闭的捕获列表;每个测试换成新的懒绑定,让捕获落在当前测试里。
    """
    monkeypatch.setattr(proxy_module, "log", structlog.get_logger("proxy"))


def token_payload(access: str = ACCESS, refresh: str = REFRESH) -> dict:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": 3600,
        "account_id": "a1",
        "subject": "user@example.com",
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
    # _env_file=None + 外部开关钉死:Nx 会把 .env 注入任务环境(见 AGENTS.md)
    return build_settings(Settings, **values)


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
    # 观测测试用:32 位 hex id 的详情/事件流路径,route 模板应归一化成 {id}。
    if path.startswith("/api/v1/runs/") and path.endswith("/events/stream"):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"event: stream.end\ndata: {}\n\n",
        )
    if path.startswith("/api/v1/runs/"):
        suffix = path.removeprefix("/api/v1/runs/")
        if len(suffix) == 32 and all(char in "0123456789abcdef" for char in suffix):
            return httpx.Response(200, json={"id": suffix})
    if path == "/api/v1/notifications/stream":
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b": ready\n\n",
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
