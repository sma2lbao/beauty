"""account_apps 凭证解析:账号行覆盖全局、半配置回退、加密存储、授权/执行生效。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from connector.services import apps
from tests.conftest import (
    ACCOUNT,
    _settings,
    demo_credentials,
    insert_account_app,
    insert_connection,
)


def _db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _provider(client: TestClient, settings, *, account_id: str = "t-1"):
    async def _run():
        async with client.app.state.session_factory() as session:
            return await apps.provider_for_account(
                session, settings, account_id, "demo"
            )

    return client.portal.call(_run)


def _stored_secret(db_path: Path) -> str:
    with sqlite3.connect(db_path) as raw:
        return raw.execute("SELECT app_secret_encrypted FROM account_apps").fetchone()[
            0
        ]


def _execute(client: TestClient, tool: str) -> dict[str, Any]:
    response = client.post(
        "/v1/connectors/demo/execute",
        json={"tool": tool, "arguments": {}, "run_id": "run-1"},
        headers={"X-Internal-Secret": "internal-test-secret", "X-Account-Id": "t-1"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_account_app_overrides_global_settings(
    client: TestClient, tmp_path: Path
) -> None:
    insert_account_app(
        _db(tmp_path), app_id="demo-account", app_secret="account-secret"
    )

    provider = _provider(client, _settings(tmp_path))

    assert provider.configured is True
    assert provider._app_id == "demo-account"
    assert provider._app_secret == "account-secret"


def test_missing_account_app_falls_back_to_global(
    client: TestClient, tmp_path: Path
) -> None:
    provider = _provider(client, _settings(tmp_path))

    assert provider._app_id == "demo-client"
    assert provider._app_secret == "demo-secret"


def test_incomplete_account_app_falls_back_to_global(
    client: TestClient, tmp_path: Path
) -> None:
    db = _db(tmp_path)
    insert_account_app(db, app_id="", app_secret="account-secret", account_id="t-1")
    settings = _settings(tmp_path)

    provider = _provider(client, settings)
    assert provider._app_id == "demo-client"
    assert provider._app_secret == "demo-secret"

    # 另一个账号:只有 app_id、secret 缺失(加密空串)
    insert_account_app(db, account_id="t-2", app_id="cli_only_id", app_secret="")
    provider_t2 = _provider(client, settings, account_id="t-2")
    assert provider_t2._app_id == "demo-client"


def test_app_secret_is_encrypted_at_rest(client: TestClient, tmp_path: Path) -> None:
    db = _db(tmp_path)
    insert_account_app(db, app_id="demo-account", app_secret="account-secret")
    settings = _settings(tmp_path)

    stored = _stored_secret(db)
    assert "account-secret" not in stored
    assert json.loads(stored)["ciphertext"].startswith("fernet:")

    encrypted = apps.encrypt_app_secret(settings, "account-secret")
    assert apps.decrypt_app_secret(settings, encrypted) == "account-secret"
    # 历史明文行仍可读(文档里的兼容约定)
    assert apps.decrypt_app_secret(settings, {"app_secret": "plain"}) == "plain"
    # 损坏/空 payload 不抛异常,交给调用方回退全局
    assert apps.decrypt_app_secret(settings, {"v": "1", "ciphertext": "bad"}) is None
    assert apps.decrypt_app_secret(settings, {}) is None


def test_authorize_uses_account_app_credentials(
    client: TestClient, demo, tmp_path: Path
) -> None:
    insert_account_app(
        _db(tmp_path), app_id="demo-account", app_secret="account-secret"
    )

    resp = client.post("/v1/connectors/demo/authorize", headers=ACCOUNT)
    assert resp.status_code == 200, resp.text
    query = parse_qs(urlparse(resp.json()["authorize_url"]).query)
    assert query["client_id"] == ["demo-account"]

    callback = client.get(
        "/v1/oauth/demo/callback",
        params={"code": "code-1", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    token_body = demo.token_calls[-1]
    assert token_body["client_id"] == "demo-account"
    assert token_body["client_secret"] == "account-secret"


def test_execute_refresh_uses_account_app_credentials(
    client: TestClient, demo, tmp_path: Path
) -> None:
    db = _db(tmp_path)
    insert_account_app(db, app_id="demo-account", app_secret="account-secret")
    insert_connection(
        db,
        expires_at=datetime.now(UTC) + timedelta(seconds=60),  # 进入刷新窗口
        credentials=demo_credentials(access_token="at-old", refresh_token="rt-old"),
    )

    payload = _execute(client, "Demo__get_me")

    assert payload["ok"] is True
    token_body = demo.token_calls[-1]
    assert token_body["grant_type"] == "refresh_token"
    assert token_body["client_id"] == "demo-account"
    assert token_body["client_secret"] == "account-secret"
