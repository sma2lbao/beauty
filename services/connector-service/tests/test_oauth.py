"""授权链路:发起授权、回调落库、state 一次性、失败语义(全部打桩,零网络)。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from connector.main import create_app
from tests.conftest import (
    _IDP,
    TENANT,
    DemoMock,
    _settings,
    insert_oauth_session,
)
from tests.demo_provider import DemoProvider


def _start(client: TestClient) -> tuple[str, str]:
    resp = client.post("/v1/connectors/demo/authorize", headers=TENANT)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    query = parse_qs(urlparse(payload["authorize_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["demo-client"]
    assert "offline_access" in query["scope"][0]
    assert query["redirect_uri"] == ["http://testserver/v1/oauth/demo/callback"]
    return payload["authorize_url"], query["state"][0]


def _callback(client: TestClient, state: str, **params: str) -> httpx.Response:
    query = {"code": "code-1", **params, "state": state}
    return client.get("/v1/oauth/demo/callback", params=query, follow_redirects=False)


def _redirect_params(response: httpx.Response) -> dict[str, str]:
    assert response.status_code == 302
    return {
        k: v[0]
        for k, v in parse_qs(urlparse(response.headers["location"]).query).items()
    }


def test_authorize_and_callback_creates_connection(
    client: TestClient, demo: DemoMock, tmp_path: Path
) -> None:
    url, state = _start(client)
    assert url.startswith("https://auth.example.test/authorize?")
    response = _callback(client, state)
    params = _redirect_params(response)
    assert params["provider"] == "demo"
    assert params["status"] == "connected"
    assert params["connection_id"]

    connections = client.get("/v1/connections", headers=TENANT).json()
    assert len(connections) == 1
    conn = connections[0]
    assert conn["status"] == "active"
    assert conn["enabled"] is True
    assert conn["account"] == "u-1"
    assert conn["display_name"] == "张三"
    assert conn["provider_account_id"] == "u-1"
    assert conn["external_workspace"] == "ws-1"
    assert conn["expires_at"] is not None
    assert conn["refresh_expires_at"] is not None
    assert "credentials" not in conn

    # 换 token 用的是授权码 + PKCE verifier
    body = demo.token_calls[0]
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "code-1"
    assert body["code_verifier"]
    assert body["redirect_uri"] == "http://testserver/v1/oauth/demo/callback"

    # 明文 token 不落库
    with sqlite3.connect(tmp_path / "test.db") as raw:
        stored = raw.execute("SELECT credentials FROM connections").fetchone()[0]
    assert "at-1" not in stored
    assert "ciphertext" in stored

    # 授权会话标记完成且记录连接
    with sqlite3.connect(tmp_path / "test.db") as raw:
        status, connection_id = raw.execute(
            "SELECT status, connection_id FROM oauth_sessions"
        ).fetchone()
    assert status == "completed"
    assert connection_id == conn["id"]


def test_callback_state_is_one_time(client: TestClient) -> None:
    _, state = _start(client)
    assert _redirect_params(_callback(client, state))["status"] == "connected"
    reused = _redirect_params(_callback(client, state))
    assert reused["status"] == "error"
    assert reused["code"] == "connector.state_used"


def test_callback_invalid_state_and_unknown_provider(client: TestClient) -> None:
    params = _redirect_params(_callback(client, "missing"))
    assert params["code"] == "connector.invalid_state"

    response = client.get(
        "/v1/oauth/notion/callback", params={"state": "x"}, follow_redirects=False
    )
    assert _redirect_params(response)["code"] == "connector.provider_not_found"


def test_callback_expired_session(client: TestClient, tmp_path: Path) -> None:
    insert_oauth_session(
        tmp_path / "test.db",
        state="old",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    params = _redirect_params(_callback(client, "old"))
    assert params["code"] == "connector.state_expired"


def test_callback_denied_by_user(client: TestClient) -> None:
    _, state = _start(client)
    params = _redirect_params(
        client.get(
            "/v1/oauth/demo/callback",
            params={"error": "access_denied", "state": state},
            follow_redirects=False,
        )
    )
    assert params["code"] == "connector.oauth_denied"


def test_callback_token_failure(
    client: TestClient, demo: DemoMock, tmp_path: Path
) -> None:
    demo.fail["/oauth/token"] = httpx.Response(400, json={"code": 20005})
    _, state = _start(client)
    params = _redirect_params(_callback(client, state))
    assert params["status"] == "error"
    assert params["code"] == "connector.auth_expired"
    assert client.get("/v1/connections", headers=TENANT).json() == []
    with sqlite3.connect(tmp_path / "test.db") as raw:
        status, error_code = raw.execute(
            "SELECT status, error_code FROM oauth_sessions"
        ).fetchone()
    assert status == "failed"
    assert error_code == "connector.auth_expired"


def test_callback_missing_open_id(client: TestClient, demo: DemoMock) -> None:
    demo.fail["/userinfo"] = httpx.Response(200, json={"code": 0, "data": {}})
    _, state = _start(client)
    params = _redirect_params(_callback(client, state))
    assert params["code"] == "connector.upstream_error"


def test_authorize_unknown_provider(client: TestClient) -> None:
    resp = client.post("/v1/connectors/notion/authorize", headers=TENANT)
    assert resp.status_code == 404
    assert resp.json()["code"] == "connector.provider_not_found"


def test_authorize_unconfigured_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 未配置应用凭证:demo provider 的默认 app_id/secret 置空
    monkeypatch.setattr(DemoProvider, "DEFAULT_APP_ID", "")
    monkeypatch.setattr(DemoProvider, "DEFAULT_APP_SECRET", "")
    settings = _settings(tmp_path)
    app = create_app(settings, token_verifier=_IDP.verifier())
    with TestClient(app) as scoped:
        catalog = scoped.get("/v1/connectors", headers=TENANT).json()["providers"][0]
        assert catalog["configured"] is False
        resp = scoped.post("/v1/connectors/demo/authorize", headers=TENANT)
        assert resp.status_code == 409
        assert resp.json()["code"] == "connector.provider_not_configured"
