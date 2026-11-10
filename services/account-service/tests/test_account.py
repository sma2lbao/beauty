"""账号域:当前账号档案(/v1/me)与设备会话。

本文件用**真实签发链路**的客户端:app 不注入假校验器,而是用 ASGI 传输
让 JwtVerifier 直接读本 app 的 JWKS 验签,register/login 拿到的是真 token。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from common.auth import JwtVerifier
from fastapi.testclient import TestClient

from account.core.config import Settings
from account.main import create_app
from tests.conftest import _register_account

GHOST_ACCOUNT_ID = "c" * 32


@pytest.fixture()
def client(tmp_path: Path):
    """本文件的客户端:真 token(本 app 签名)+ 真验签(本 app JWKS)。"""
    settings = Settings(
        _env_file=None,
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        auto_create_tables=True,
    )
    app = create_app(settings)
    verifier = JwtVerifier(
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        transport=httpx.ASGITransport(app=app),
    )
    app.state.token_verifier = verifier
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(verifier.aclose())


def _bearer(client: TestClient, account_id: str, subject: str = "ghost@example.com"):
    """用 app 自己的 signer 签一个指定 account_id 的 token(测悬空账号等)。"""
    token = client.app.state.signer.sign(subject=subject, account_id=account_id)
    return {"Authorization": f"Bearer {token}"}


def test_me_returns_registered_account(client: TestClient) -> None:
    registered = _register_account(client)
    me = client.get(
        "/v1/me", headers={"Authorization": f"Bearer {registered['access_token']}"}
    )
    assert me.status_code == 200
    body = me.json()
    assert body["id"] == registered["account_id"]
    assert body["email"] == "alice@example.com"
    assert body["display_name"] == "alice"  # 未填则回落 email 前缀
    assert body["status"] == "active"
    assert body["created_at"]
    assert set(body) == {
        "id",
        "email",
        "display_name",
        "status",
        "role",
        "created_at",
    }


def test_me_unknown_account_is_404(client: TestClient) -> None:
    # token 合法但账号行不存在(如账号已注销):404,不泄漏存在性细节
    response = client.get("/v1/me", headers=_bearer(client, GHOST_ACCOUNT_ID))
    assert response.status_code == 404
    assert response.json()["code"] == "account.account_not_found"


def test_patch_me_only_display_name(client: TestClient) -> None:
    registered = _register_account(client)
    headers = {"Authorization": f"Bearer {registered['access_token']}"}

    patched = client.patch("/v1/me", json={"display_name": "小月"}, headers=headers)
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "小月"
    # email/status 由服务端维护,请求里的多余字段被忽略
    unchanged = client.patch(
        "/v1/me",
        json={"display_name": "月亮", "email": "evil@example.com", "status": "x"},
        headers=headers,
    ).json()
    assert unchanged["email"] == "alice@example.com"
    assert unchanged["status"] == "active"
    assert unchanged["display_name"] == "月亮"


def test_patch_me_null_display_name_falls_back_to_email_prefix(
    client: TestClient,
) -> None:
    registered = _register_account(client)
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    client.patch("/v1/me", json={"display_name": "小月"}, headers=headers)
    cleared = client.patch(
        "/v1/me", json={"display_name": None}, headers=headers
    ).json()
    assert cleared["display_name"] == "alice"


def test_me_is_scoped_per_account(client: TestClient) -> None:
    alpha = _register_account(client, email="alpha@example.com")
    beta = _register_account(client, email="beta@example.com")
    assert alpha["account_id"] != beta["account_id"]

    me_alpha = client.get(
        "/v1/me", headers={"Authorization": f"Bearer {alpha['access_token']}"}
    ).json()
    me_beta = client.get(
        "/v1/me", headers={"Authorization": f"Bearer {beta['access_token']}"}
    ).json()
    assert me_alpha["id"] == alpha["account_id"]
    assert me_beta["id"] == beta["account_id"]
    assert me_alpha["email"] == "alpha@example.com"


def test_sessions_register_list_and_revoke(client: TestClient) -> None:
    registered = _register_account(client)
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    # 注册流程本身登记了一个会话
    assert registered["session_id"]
    assert len(client.get("/v1/sessions", headers=headers).json()) == 1

    created = client.post(
        "/v1/sessions", json={"device": "MacBook / Chrome"}, headers=headers
    )
    assert created.status_code == 201
    session = created.json()
    assert session["device"] == "MacBook / Chrome"
    assert session["status"] == "active"
    assert set(session) == {
        "id",
        "device",
        "status",
        "last_seen_at",
        "created_at",
    }

    assert len(client.get("/v1/sessions", headers=headers).json()) == 2

    revoked = client.post(
        f"/v1/sessions/{session['id']}/revoke", headers=headers
    ).json()
    assert revoked["status"] == "revoked"

    again = client.post(f"/v1/sessions/{session['id']}/revoke", headers=headers)
    assert again.status_code == 409
    assert again.json()["code"] == "account.session_revoked"


def test_session_isolation_cross_account_is_404(client: TestClient) -> None:
    alpha = _register_account(client, email="alpha@example.com")
    beta = _register_account(client, email="beta@example.com")
    alpha_headers = {"Authorization": f"Bearer {alpha['access_token']}"}
    beta_headers = {"Authorization": f"Bearer {beta['access_token']}"}

    session = client.post(
        "/v1/sessions", json={"device": "iPad"}, headers=alpha_headers
    ).json()
    # 别账号看不到(404 而不是 403,避免泄漏存在性)
    cross = client.post(f"/v1/sessions/{session['id']}/revoke", headers=beta_headers)
    assert cross.status_code == 404
    assert cross.json()["code"] == "account.session_not_found"
    assert len(client.get("/v1/sessions", headers=beta_headers).json()) == 1


def test_session_requires_existing_account(client: TestClient) -> None:
    response = client.post(
        "/v1/sessions",
        json={"device": "iPad"},
        headers=_bearer(client, GHOST_ACCOUNT_ID),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "account.account_not_found"


def test_legacy_organization_and_users_endpoints_removed(client: TestClient) -> None:
    # 组织/成员产品层已拆除:旧端点不再挂载
    headers = _bearer(client, GHOST_ACCOUNT_ID)
    assert client.get("/v1/organization", headers=headers).status_code == 404
    assert client.get("/v1/users", headers=headers).status_code == 404
    assert client.post("/v1/users", json={}, headers=headers).status_code == 404
