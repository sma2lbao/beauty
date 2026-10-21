"""MCP 浏览器 OAuth 同源回调:302 回前端,且只把 state/code 转给 orchestrator。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from bff.main import create_app
from tests.conftest import settings

CALLBACK = "/api/mcp/oauth/callback"


def _client(handler, **overrides) -> TestClient:
    app = create_app(settings(**overrides), transport=httpx.MockTransport(handler))
    return TestClient(app)


def _query(location: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(location).query)


def _upstream_ok(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/internal/mcp/oauth/callback"
    assert request.headers["x-internal-secret"] == "internal-secret"
    return httpx.Response(
        200, json={"ok": True, "redirect_path": "/mcp?oauth=success&server=linear"}
    )


def test_callback_forwards_state_and_code_then_redirects() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"body": request.content, "path": request.url.path})
        return _upstream_ok(request)

    with _client(handler) as client:
        response = client.get(
            CALLBACK,
            params={"state": "signed", "code": "code-1"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "http://localhost:5173/mcp?oauth=success&server=linear"
    )
    assert len(seen) == 1
    assert b'"state":"signed"' in seen[0]["body"]
    assert b'"code":"code-1"' in seen[0]["body"]


def test_callback_redirects_to_error_when_provider_denied() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    with _client(handler) as client:
        response = client.get(
            CALLBACK,
            params={
                "state": "s",
                "error": "access_denied",
                "error_description": "用户拒绝",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert (
        response.headers["location"]
        == "http://localhost:5173/mcp?oauth=error&error=%E7%94%A8%E6%88%B7%E6%8B%92%E7%BB%9D"
    )
    assert called is False


def test_callback_requires_state_and_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("缺少参数时不应调用上游")

    with _client(handler) as client:
        response = client.get(CALLBACK, params={"state": "s"}, follow_redirects=False)

    assert response.status_code == 302
    assert _query(response.headers["location"])["oauth"] == ["error"]
    assert "code" in _query(response.headers["location"])["error"][0]


def test_callback_requires_internal_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("未配置内部凭证时不应调用上游")

    with _client(handler, internal_secret="") as client:
        response = client.get(
            CALLBACK, params={"state": "s", "code": "c"}, follow_redirects=False
        )

    assert response.status_code == 302
    assert _query(response.headers["location"])["oauth"] == ["error"]
    assert "内部调用凭证" in _query(response.headers["location"])["error"][0]


def test_callback_reports_upstream_rejection() -> None:
    with _client(
        lambda request: httpx.Response(401, json={"code": "internal.forbidden"})
    ) as client:
        response = client.get(
            CALLBACK, params={"state": "s", "code": "c"}, follow_redirects=False
        )

    assert response.status_code == 302
    assert "重新发起授权" in _query(response.headers["location"])["error"][0]


def test_callback_reports_upstream_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with _client(handler) as client:
        response = client.get(
            CALLBACK, params={"state": "s", "code": "c"}, follow_redirects=False
        )

    assert response.status_code == 302
    assert "暂时不可用" in _query(response.headers["location"])["error"][0]


@pytest.mark.parametrize(
    "body", [{"ok": False, "redirect_path": "/mcp?oauth=error&error=bad"}, {}]
)
def test_callback_uses_upstream_redirect_path(body: dict) -> None:
    with _client(lambda request: httpx.Response(200, json=body)) as client:
        response = client.get(
            CALLBACK, params={"state": "s", "code": "c"}, follow_redirects=False
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:5173/mcp?oauth=")
