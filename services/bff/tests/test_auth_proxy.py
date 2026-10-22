from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from bff.main import create_app
from tests.conftest import handler as upstream_handler
from tests.conftest import login, settings

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_login_sets_httponly_cookie_and_hides_tokens(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "password123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "user@example.com"
    assert "access_token" not in body
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "bm_session=" in set_cookie

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["user"]["tenant_id"] == "t1"


def test_refresh_and_logout_require_csrf(client: TestClient) -> None:
    csrf = login(client)

    assert client.post("/api/auth/refresh").status_code == 403
    refreshed = client.post("/api/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert refreshed.status_code == 200
    assert refreshed.json()["user"]["user_id"] == "u1"

    # refresh 会轮换 CSRF cookie,后续请求要用新的
    rotated = client.cookies.get("bm_csrf")
    assert rotated
    assert client.post("/api/auth/logout").status_code == 403
    assert (
        client.post("/api/auth/logout", headers={"X-CSRF-Token": rotated}).status_code
        == 204
    )
    assert client.get("/api/auth/session").status_code == 401


def test_proxy_injects_bearer_and_passes_response(client: TestClient) -> None:
    login(client)
    response = client.get("/api/v1/runs")
    assert response.status_code == 200
    assert response.json()[0]["id"] == "r1"


def test_proxy_refreshes_once_on_401(client: TestClient) -> None:
    login(client)
    response = client.get("/api/v1/runs/expired")
    assert response.status_code == 200
    assert response.json()["id"] == "r1"
    # refresh 后 Cookie 已轮换,后续 session 仍有效
    assert client.get("/api/auth/session").status_code == 200


def test_file_download_streams_bytes(client: TestClient) -> None:
    login(client)
    response = client.get("/api/files/a1")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text == "# 周报"


def test_stream_token_requires_session(client: TestClient) -> None:
    csrf = login(client)
    response = client.post(
        "/api/stream-token",
        json={"run_id": "r1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["token"] == "stream-token"
    assert response.json()["url"].endswith("/api/v1/runs/r1/events/stream")


def test_oauth_start_redirects_to_provider(client: TestClient) -> None:
    response = client.get("/api/auth/oauth/google/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth"
    )


def test_oauth_callback_sets_session(client: TestClient) -> None:
    response = client.get(
        "/api/auth/oauth/google/callback",
        params={"code": "code-1", "state": "signed-state"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].endswith("/")
    assert client.get("/api/auth/session").status_code == 200


def test_proxy_streams_sse(client: TestClient) -> None:
    login(client)
    response = client.get("/api/v1/runs/r1/events/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "stream.end" in response.text


def test_proxy_streams_sse_by_path_without_accept(client: TestClient) -> None:
    """客户端没声明 Accept 也要流式转发:否则长连接被缓冲到读超时 → 500。"""
    login(client)
    response = client.get("/api/v1/notifications/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "ready" in response.text


def test_proxy_forwards_request_id_and_traceparent() -> None:
    """观测链路:入站 request_id/traceparent 透传给下游,并在响应头回写。"""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return upstream_handler(request)

    app = create_app(settings(), transport=httpx.MockTransport(recording))
    with TestClient(app) as client:
        login(client)
        response = client.get(
            "/api/v1/runs",
            headers={"X-Request-ID": "req-abc-123", "traceparent": TRACEPARENT},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-abc-123"
    proxied = next(item for item in seen if item.url.path.endswith("/v1/runs"))
    assert proxied.headers["x-request-id"] == "req-abc-123"
    assert proxied.headers["traceparent"] == TRACEPARENT
