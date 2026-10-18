"""OAuth:授权 URL、state 签名、code 交换与首次登录建租户。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from account.core.config import Settings
from account.main import create_app


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith(("/token", "/access_token")):
        return httpx.Response(200, json={"access_token": "provider-token"})
    if request.url.path.endswith("/userinfo"):
        return httpx.Response(
            200,
            json={
                "sub": "google-sub-1",
                "email": "oauth-user@example.com",
                "name": "OAuth User",
            },
        )
    if request.url.path == "/user":
        return httpx.Response(
            200, json={"id": 42, "login": "octocat", "name": "Octo", "email": None}
        )
    if request.url.path == "/user/emails":
        return httpx.Response(
            200,
            json=[{"email": "octo@example.com", "primary": True, "verified": True}],
        )
    return httpx.Response(404, json={"code": "not_found"})


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/oauth.db",
        auto_create_tables=True,
        internal_secret="internal-secret",
        oauth_state_secret="oauth-state-secret",
        google_client_id="google-client",
        google_client_secret="google-secret",
        github_client_id="github-client",
        github_client_secret="github-secret",
    )


@pytest.fixture()
def oauth_client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(_settings(tmp_path), oauth_transport=httpx.MockTransport(_handler))
    with TestClient(app) as client:
        yield client


def test_google_authorize_and_exchange(oauth_client: TestClient) -> None:
    authorize = oauth_client.get(
        "/v1/auth/oauth/google/authorize",
        params={"redirect_uri": "http://localhost:5173/api/auth/oauth/google/callback"},
    )
    assert authorize.status_code == 200
    body = authorize.json()
    assert body["authorization_url"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )
    assert "code_challenge=" in body["authorization_url"]

    exchanged = oauth_client.post(
        "/v1/auth/oauth/google/exchange",
        json={"code": "provider-code", "state": body["state"]},
    )
    assert exchanged.status_code == 200
    token = exchanged.json()
    assert token["subject"] == "oauth-user@example.com"
    assert token["access_token"]
    assert token["refresh_token"]

    # 同一身份再次登录复用用户/组织
    second = oauth_client.post(
        "/v1/auth/oauth/google/exchange",
        json={"code": "provider-code", "state": body["state"]},
    )
    assert second.status_code == 200
    assert second.json()["user_id"] == token["user_id"]
    assert second.json()["tenant_id"] == token["tenant_id"]


def test_github_exchange_uses_primary_email(oauth_client: TestClient) -> None:
    authorize = oauth_client.get(
        "/v1/auth/oauth/github/authorize",
        params={"redirect_uri": "http://localhost:5173/api/auth/oauth/github/callback"},
    )
    assert authorize.status_code == 200
    state = authorize.json()["state"]

    exchanged = oauth_client.post(
        "/v1/auth/oauth/github/exchange",
        json={"code": "provider-code", "state": state},
    )
    assert exchanged.status_code == 200
    assert exchanged.json()["subject"] == "octo@example.com"


def test_oauth_provider_disabled(tmp_path: Path) -> None:
    settings = Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/no-oauth.db",
        auto_create_tables=True,
    )
    app = create_app(settings, oauth_transport=httpx.MockTransport(_handler))
    with TestClient(app) as client:
        response = client.get(
            "/v1/auth/oauth/google/authorize",
            params={"redirect_uri": "http://localhost:5173/callback"},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "oauth.provider_disabled"
