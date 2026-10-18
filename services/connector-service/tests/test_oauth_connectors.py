"""Gmail/M365 OAuth 连接(M3 阶段 3A)。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient

from connector.core.config import Settings
from connector.core.crypto import decrypt_credentials
from connector.main import create_app
from connector.services.oauth_mail import GmailTransport
from tests.conftest import _IDP, TENANT


def _oauth_settings(tmp_path: Path) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        auto_create_tables=True,
        google_client_id="google-client",
        google_client_secret="google-secret",
        microsoft_client_id="ms-client",
        microsoft_client_secret="ms-secret",
        oauth_redirect_base="http://127.0.0.1:8004/v1/oauth",
        oauth_web_base="http://localhost:5173",
    )


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/token"):
        return httpx.Response(
            200,
            json={
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
            },
        )
    if request.url.path.endswith("/userinfo"):
        return httpx.Response(200, json={"email": "me@gmail.com"})
    return httpx.Response(404)


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        _oauth_settings(tmp_path),
        transport_factory=lambda conn: None,
        token_verifier=_IDP.verifier(),
        oauth_transport=httpx.MockTransport(_handler),
    )
    return TestClient(app)


def test_providers_and_authorize_url(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        providers = client.get("/v1/oauth/providers")
        assert providers.status_code == 200
        assert {item["provider"]: item["enabled"] for item in providers.json()} == {
            "google": True,
            "microsoft": True,
        }
        started = client.get("/v1/oauth/google/start", headers=TENANT)
        assert started.status_code == 200
        url = started.json()["authorize_url"]
        assert "accounts.google.com" in url
        query = parse_qs(urlsplit(url).query)
        assert query["redirect_uri"] == ["http://127.0.0.1:8004/v1/oauth/google/callback"]
        assert query["state"][0]


def test_callback_creates_encrypted_connection(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        started = client.get("/v1/oauth/google/start", headers=TENANT).json()
        state = parse_qs(urlsplit(started["authorize_url"]).query)["state"][0]
        callback = client.get(
            f"/v1/oauth/google/callback?code=code-1&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code in (302, 307)
        assert callback.headers["location"].startswith(
            "http://localhost:5173/connectors?"
        )
        assert "oauth=success" in callback.headers["location"]

        listing = client.get("/v1/connections", headers=TENANT)
        assert listing.status_code == 200
        assert listing.json()[0]["provider"] == "gmail"
        assert listing.json()[0]["account"] == "me@gmail.com"

        import sqlite3

        conn = sqlite3.connect(tmp_path / "test.db")
        stored = json.loads(
            conn.execute("select credentials from connections").fetchone()[0]
        )
        conn.close()
        assert "refresh-1" not in str(stored)
        decrypted = decrypt_credentials(
            "dev-connector-secret-change-me", stored
        )
        assert decrypted["refresh_token"] == "refresh-1"


def test_invalid_state_redirects_error(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        callback = client.get(
            "/v1/oauth/google/callback?code=code-1&state=bad.state",
            follow_redirects=False,
        )
        assert callback.status_code in (302, 307)
        assert "oauth=error" in callback.headers["location"]


def test_gmail_transport_parses_thread_and_attachments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if request.url.path.endswith("/messages/m1"):
            return httpx.Response(
                200,
                json={
                    "id": "m1",
                    "threadId": "t1",
                    "snippet": "hello",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Hi"},
                            {"name": "From", "value": "a@example.com"},
                            {"name": "Date", "value": "today"},
                        ],
                        "parts": [{"filename": "a.pdf"}],
                    },
                },
            )
        return httpx.Response(404)

    import asyncio

    transport = GmailTransport(
        "me@gmail.com",
        {"access_token": "a", "expires_at": int(time.time()) + 3600},
        client_id="id",
        client_secret="secret",
        transport=httpx.MockTransport(handler),
    )

    async def scenario():
        return await transport.fetch(5)

    emails = asyncio.run(scenario())
    assert emails[0].thread_id == "t1"
    assert emails[0].has_attachments is True
    assert emails[0].subject == "Hi"
