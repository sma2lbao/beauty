"""MCP OAuth 2.1(M3-04):元数据、注册、PKCE、token、introspect/refresh。"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from account.main import create_app
from tests.conftest import _IDP, TENANT, _settings


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        _settings(tmp_path, internal_secret="internal-secret"),
        token_verifier=_IDP.verifier(),
    )
    return TestClient(app)


def _pkce() -> tuple[str, str]:
    verifier = "verifier-" + "x" * 40
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _register(client: TestClient) -> dict:
    response = client.post(
        "/v1/oauth/register",
        json={
            "client_name": "MCP Test Client",
            "redirect_uris": ["http://127.0.0.1:9876/callback"],
            "scope": "mcp:tools:read mcp:tools:call",
        },
    )
    assert response.status_code == 201
    return response.json()


def _authorize(client: TestClient, registered: dict, challenge: str):
    return client.post(
        "/v1/oauth/authorize/approve",
        json={
            "client_id": registered["client_id"],
            "redirect_uri": registered["redirect_uris"][0],
            "scope": "mcp:tools:read",
            "state": "xyz",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        headers=TENANT,
    )


def test_metadata_and_registration(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        metadata = client.get("/.well-known/oauth-authorization-server")
        assert metadata.status_code == 200
        body = metadata.json()
        assert body["code_challenge_methods_supported"] == ["S256"]
        assert body["authorization_endpoint"].endswith("/oauth/authorize")

        registered = _register(client)
        assert registered["client_id"].startswith("bm_client_")
        assert registered["token_endpoint_auth_method"] == "none"
        info = client.get(f"/v1/oauth/clients/{registered['client_id']}")
        assert info.status_code == 200
        assert info.json()["client_name"] == "MCP Test Client"


def test_pkce_code_exchange_introspect_and_rotation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        registered = _register(client)
        verifier, challenge = _pkce()
        authorized = _authorize(client, registered, challenge)
        assert authorized.status_code == 200
        redirect_to = authorized.json()["redirect_to"]
        assert "state=xyz" in redirect_to
        code = redirect_to.split("code=", 1)[1].split("&", 1)[0]

        token = client.post(
            "/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": registered["client_id"],
                "redirect_uri": registered["redirect_uris"][0],
                "code_verifier": verifier,
            },
        )
        assert token.status_code == 200
        tokens = token.json()
        assert tokens["token_type"] == "Bearer"
        assert tokens["refresh_token"].startswith("bm_refresh_")

        replay = client.post(
            "/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": registered["client_id"],
                "redirect_uri": registered["redirect_uris"][0],
                "code_verifier": verifier,
            },
        )
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"

        introspected = client.post(
            "/v1/oauth/introspect",
            json={"token": tokens["access_token"]},
            headers={"X-Internal-Secret": "internal-secret"},
        )
        assert introspected.status_code == 200
        assert introspected.json()["active"] is True
        assert introspected.json()["scope"] == "mcp:tools:read"

        refreshed = client.post(
            "/v1/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": registered["client_id"],
            },
        )
        assert refreshed.status_code == 200
        old_refresh = client.post(
            "/v1/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": registered["client_id"],
            },
        )
        assert old_refresh.status_code == 400


def test_pkce_and_redirect_validation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        registered = _register(client)
        _, challenge = _pkce()
        bad_redirect = client.post(
            "/v1/oauth/authorize/approve",
            json={
                "client_id": registered["client_id"],
                "redirect_uri": "http://127.0.0.1:9876/evil",
                "scope": "mcp:tools:read",
                "code_challenge": challenge,
            },
            headers=TENANT,
        )
        assert bad_redirect.status_code == 400

        authorized = _authorize(client, registered, challenge)
        code = authorized.json()["redirect_to"].split("code=", 1)[1].split("&", 1)[0]
        wrong_verifier = client.post(
            "/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": registered["client_id"],
                "redirect_uri": registered["redirect_uris"][0],
                "code_verifier": "wrong-verifier",
            },
        )
        assert wrong_verifier.status_code == 400
        assert wrong_verifier.json()["error"] == "invalid_grant"
