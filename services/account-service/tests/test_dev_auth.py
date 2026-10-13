"""dev IdP 桩测试:登录幂等、OIDC 端点、与生产 JwtVerifier 的兼容闭环。

关键在最后两组用例:`JwtVerifier`(六个服务运行时用的**同一个类**)经
HTTP 发现文档 + JWKS 验桩签的 token,过期即 401——证明桩与生产验证
路径完全兼容,没有任何后门;换 Logto 只是换 issuer。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from common.auth import JwtVerifier
from common.errors import UnauthorizedError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from account.core.config import Settings
from account.core.dev_idp import DevIdP
from account.main import create_app

ISSUER = "http://auth-test.local"
AUDIENCE = "beauty-api"


def _auth_settings(db_path, **dev_overrides) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        auto_create_tables=True,
        auth_issuer=ISSUER,
        dev_auth_enabled=True,
        **dev_overrides,
    )


@pytest.fixture()
def app(tmp_path: Path) -> FastAPI:
    return create_app(_auth_settings(tmp_path / "auth.db"))


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_disabled_by_default(tmp_path: Path) -> None:
    """关桩时端点不存在:登录 404,OIDC 发现/JWKS 也不可见。"""
    settings = Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/off.db",
        auto_create_tables=True,
    )
    with TestClient(create_app(settings)) as off:
        assert off.post("/v1/auth/login", json={"email": "a@b.co"}).status_code == 404
        assert off.get("/.well-known/openid-configuration").status_code == 404
        assert off.get("/jwks").status_code == 404


def test_login_creates_owner(client: TestClient) -> None:
    """首登:幂等建组织,首个成员是 owner,返回可用的 access token。"""
    resp = client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "organization_id": "org-alpha"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_id"] == "org-alpha"
    assert body["role"] == "owner"
    assert body["expires_in"] == 3600
    assert body["access_token"].count(".") == 2  # JWS 三段


def test_login_idempotent_same_org(client: TestClient) -> None:
    """同 email 重复登录:成员复用(不 409),会话新开,第二人是 member。"""
    first = client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "organization_id": "org-alpha"},
    ).json()
    again = client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "organization_id": "org-alpha"},
    ).json()
    assert again["user_id"] == first["user_id"]
    assert again["session_id"] != first["session_id"]

    second = client.post(
        "/v1/auth/login",
        json={"email": "bob@example.com", "organization_id": "org-alpha"},
    ).json()
    assert second["role"] == "member"


def test_oidc_endpoints(client: TestClient) -> None:
    """发现文档 + JWKS:JwtVerifier 靠它们自动发现密钥。"""
    discovery = client.get("/.well-known/openid-configuration")
    assert discovery.status_code == 200
    assert discovery.json()["issuer"] == ISSUER
    assert discovery.json()["jwks_uri"] == f"{ISSUER}/jwks"

    keys = client.get("/jwks")
    assert keys.status_code == 200
    key = keys.json()["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert key["kid"]


async def test_verifier_accepts_token(app: FastAPI, client: TestClient) -> None:
    """生产验证路径闭环:JwtVerifier 经发现文档 + JWKS 验桩签的 token。"""
    body = client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "organization_id": "org-alpha"},
    ).json()
    verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        transport=httpx.ASGITransport(app=app),
    )
    principal = await verifier.verify(body["access_token"])
    assert principal.subject == "alice@example.com"
    assert principal.tenant_id == "org-alpha"
    await verifier.aclose()


async def test_verifier_rejects_expired(app: FastAPI, client: TestClient) -> None:
    """过期 token 必须被验签拒绝(401),不因是桩而放行。"""
    stale_idp = DevIdP(issuer=ISSUER, audience=AUDIENCE, ttl_seconds=-10)
    token = stale_idp.sign(subject="alice@example.com", tenant_id="org-alpha")
    verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        transport=httpx.ASGITransport(app=app),
    )
    with pytest.raises(UnauthorizedError):
        await verifier.verify(token)
    await verifier.aclose()


async def test_tenant_claim_isolation(app: FastAPI, client: TestClient) -> None:
    """两个组织的 token:租户 claim 各自正确,互相不可冒充。"""
    alpha = client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "organization_id": "org-alpha"},
    ).json()
    beta = client.post(
        "/v1/auth/login",
        json={"email": "alice@example.com", "organization_id": "org-beta"},
    ).json()
    verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        transport=httpx.ASGITransport(app=app),
    )
    assert (await verifier.verify(alpha["access_token"])).tenant_id == "org-alpha"
    assert (await verifier.verify(beta["access_token"])).tenant_id == "org-beta"
    await verifier.aclose()


def _issue(client: TestClient, tenant: str, secret: str | None) -> object:
    headers = {"X-Internal-Secret": secret} if secret else {}
    return client.post("/v1/auth/issue", json={"tenant_id": tenant}, headers=headers)


def test_issue_requires_internal_secret(client: TestClient) -> None:
    """未配置密钥时内部签发关闭:无论带不带头都 401。"""
    resp = client.post("/v1/auth/issue", json={"tenant_id": "t-a"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "auth.internal_forbidden"
    assert _issue(client, "t-a", "anything").status_code == 401


async def test_issue_swaps_tenant_token(tmp_path: Path) -> None:
    """配置密钥后:内部调用可按目标租户换标准 token,verifier 全链路认可。"""
    secret = "dev-secret-1"
    settings = _auth_settings(tmp_path / "issue.db", dev_internal_secret=secret)
    app = create_app(settings)
    with TestClient(app) as scoped:
        bad = _issue(scoped, "t-a", "wrong-secret")
        assert bad.status_code == 401

        ok = _issue(scoped, "t-a", secret)
        assert ok.status_code == 200
        body = ok.json()
        assert body["tenant_id"] == "t-a"
        assert body["subject"] == "svc-orchestrator"

        verifier = JwtVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            transport=httpx.ASGITransport(app=app),
        )
        principal = await verifier.verify(body["access_token"])
        assert principal.tenant_id == "t-a"
        await verifier.aclose()
