"""静态令牌连接(平台能力):粘贴 provider 令牌建连接,不需要 OAuth/回调。

用测试专用 demo provider 驱动:provider 声明 token_connectable=True 并实现
connect_with_token(先 GET /userinfo 校验,再落库)。服务层与 Notion/其它 SaaS 无关。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from common.service_testkit import app_client
from fastapi.testclient import TestClient

from connector.core.config import Settings
from connector.main import create_app
from tests.conftest import _IDP, INTERNAL_HEADERS, TENANT, DemoMock, _settings
from tests.demo_provider import DemoProvider

TOKEN = "static-token-1"


def _settings_obj(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"_env_file": None, "env": "dev"}
    values.update(overrides)
    return Settings(**values)


def _client(mock: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(mock.handler))


def _token_app(tmp_path: Path, mock: Any) -> TestClient:
    return create_app(
        _settings(tmp_path, providers_enabled="demo"),
        token_verifier=_IDP.verifier(),
        http_transport=httpx.MockTransport(mock.handler),
    )


def _error_response(status: int, code: int) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "msg": "failed"})


async def test_connect_with_token_returns_static_credentials() -> None:
    mock = DemoMock()
    async with _client(mock) as client:
        credentials, identity = await DemoProvider(_settings_obj()).connect_with_token(
            TOKEN, client
        )
    assert credentials.access_token == TOKEN
    assert credentials.refresh_token is None
    assert credentials.expires_at is None
    assert identity.account == "token:u-1"
    assert identity.display_name == "张三"
    assert identity.external_workspace == "ws-1"
    assert identity.metadata["connect"] == "token"
    assert mock.api_calls[0] == (
        "GET",
        "https://api.example.test/userinfo",
        f"Bearer {TOKEN}",
    )


def test_token_connect_creates_connection_without_app_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 模拟「没配应用凭证」:OAuth 按钮不可用,但令牌路径仍可连
    monkeypatch.setattr(DemoProvider, "DEFAULT_APP_ID", "")
    monkeypatch.setattr(DemoProvider, "DEFAULT_APP_SECRET", "")
    mock = DemoMock()
    app = _token_app(tmp_path, mock)
    with app_client(app) as client:
        entry = client.get("/v1/connectors", headers=TENANT).json()["providers"][0]
        assert entry["name"] == "demo"
        assert entry["token_connectable"] is True
        assert entry["configured"] is False
        assert entry["connected"] is False

        resp = client.post(
            "/v1/connections",
            headers=TENANT,
            json={
                "provider": "demo",
                "token": TOKEN,
                "display_name": "我的示例连接",
            },
        )
        assert resp.status_code == 201, resp.text
        connection = resp.json()
        assert connection["provider"] == "demo"
        assert connection["account"] == "token:u-1"
        assert connection["auth_kind"] == "token"
        assert connection["display_name"] == "我的示例连接"
        assert connection["external_workspace"] == "ws-1"
        assert connection["status"] == "active"
        assert connection["enabled"] is True
        assert connection["expires_at"] is None
        assert "credentials" not in connection
        assert connection["scopes"] == []
        assert mock.api_calls[0][2] == f"Bearer {TOKEN}"

        entry = client.get("/v1/connectors", headers=TENANT).json()["providers"][0]
        assert entry["connected"] is True
        assert entry["connection"]["id"] == connection["id"]

        # 执行面直接用这条令牌,不需要额外授权
        executed = client.post(
            "/v1/connectors/demo/execute",
            headers=INTERNAL_HEADERS,
            json={"tool": "Demo__get_me", "arguments": {}},
        )
        assert executed.status_code == 200, executed.text
        body = executed.json()
        assert body["ok"] is True
        assert body["connection_id"] == connection["id"]
        assert mock.api_calls[-1][2] == f"Bearer {TOKEN}"

        # 同一租户/provider/account 重复粘贴复用同一行(幂等)
        again = client.post(
            "/v1/connections",
            headers=TENANT,
            json={"provider": "demo", "token": TOKEN},
        )
        assert again.status_code == 201
        assert again.json()["id"] == connection["id"]
        assert len(client.get("/v1/connections", headers=TENANT).json()) == 1


def test_token_connect_rejects_invalid_token(tmp_path: Path) -> None:
    mock = DemoMock()
    mock.fail["/userinfo"] = _error_response(401, 99991663)
    app = _token_app(tmp_path, mock)
    with app_client(app) as client:
        resp = client.post(
            "/v1/connections",
            headers=TENANT,
            json={"provider": "demo", "token": "broken-token"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "connector.invalid_credentials"
        assert "令牌" in body["message"]
        assert body["details"]["retryable"] is False
        # 校验失败不落库
        assert client.get("/v1/connections", headers=TENANT).json() == []


def test_token_connect_rejects_unsupported_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = DemoMock()
    app = _token_app(tmp_path, mock)
    monkeypatch.setattr(DemoProvider, "token_connectable", False)
    with app_client(app) as client:
        resp = client.post(
            "/v1/connections",
            headers=TENANT,
            json={"provider": "demo", "token": TOKEN},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "connector.token_not_supported"
        assert mock.api_calls == []


def test_token_connect_unknown_provider(tmp_path: Path) -> None:
    mock = DemoMock()
    app = _token_app(tmp_path, mock)
    with app_client(app) as client:
        resp = client.post(
            "/v1/connections",
            headers=TENANT,
            json={"provider": "acme", "token": TOKEN},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "connector.provider_not_found"
        assert mock.api_calls == []
