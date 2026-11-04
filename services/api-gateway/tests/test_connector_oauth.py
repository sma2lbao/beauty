"""连接器浏览器 OAuth 同源回调:公开路由,透传 connector-service 的 302。

回调是第三方授权页重定向回来的,**没有** Authorization 头也没有会话 Cookie,
所以它必须绕过通用 /api 代理的鉴权;这里锁住这个行为。
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from api_gateway.main import create_app
from tests.conftest import settings

CALLBACK = "/api/connector/v1/oauth/notion/callback"
SUCCESS = "http://localhost:5173/connectors?provider=notion&status=connected"


def _client(handler, **overrides) -> TestClient:
    app = create_app(
        settings(connector_base_url="http://connector", **overrides),
        transport=httpx.MockTransport(handler),
    )
    return TestClient(app)


def test_callback_is_public_and_passes_upstream_redirect_through() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": SUCCESS})

    with _client(handler) as client:
        # 不带任何 Cookie / Authorization:浏览器被授权页重定向回来时就是这样
        response = client.get(
            CALLBACK, params={"code": "c-1", "state": "s-1"}, follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"] == SUCCESS
    assert seen == ["http://connector/v1/oauth/notion/callback?code=c-1&state=s-1"]


def test_callback_forwards_provider_denied_query() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.query.decode())
        return httpx.Response(
            302,
            headers={
                "location": "http://localhost:5173/connectors?provider=notion&status=error"
            },
        )

    with _client(handler) as client:
        response = client.get(
            CALLBACK, params={"error": "access_denied"}, follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"].endswith("provider=notion&status=error")
    assert seen == ["error=access_denied"]


def test_callback_redirects_to_error_page_when_upstream_rejects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "connector.oauth_failed"})

    with _client(handler) as client:
        response = client.get(
            CALLBACK, params={"code": "bad", "state": "s"}, follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "http://localhost:5173/connectors?status=error&code=connector.oauth_failed"
    )


def test_callback_redirects_to_error_page_when_upstream_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with _client(handler) as client:
        response = client.get(
            CALLBACK, params={"code": "c", "state": "s"}, follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "http://localhost:5173/connectors?status=error&code=connector.upstream_unavailable"
    )


def test_callback_rejects_unknown_provider_without_calling_upstream() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(302, headers={"location": SUCCESS})

    with _client(handler) as client:
        response = client.get(
            "/api/connector/v1/oauth/Notion/callback",
            params={"code": "c", "state": "s"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "http://localhost:5173/connectors?status=error&code=connector.oauth_failed"
    )
    assert called is False
