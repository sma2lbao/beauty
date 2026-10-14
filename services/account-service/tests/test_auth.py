"""自建认证测试:注册/登录/刷新旋转/重用吊销/登出 + 验签闭环。

关键断言:
- `JwtVerifier`(六个服务运行时用的**同一个类**)经 HTTP 发现文档 + JWKS
  验自签 token,过期即 401——与将来换托管 IdP 的验证路径完全一致;
- 刷新令牌**一次性**:重用已作废令牌会吊销该用户全部刷新令牌(泄露防御);
- 登录失败不区分"用户不存在"与"密码错"(防枚举)。
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
from account.core.idp import IdPSigner
from account.main import create_app

ISSUER = "http://auth-test.local"
AUDIENCE = "beauty-api"


def _auth_settings(db_path, **overrides) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        auto_create_tables=True,
        auth_issuer=ISSUER,
        **overrides,
    )


@pytest.fixture()
def app(tmp_path: Path) -> FastAPI:
    return create_app(_auth_settings(tmp_path / "auth.db"))


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _register(
    client: TestClient, email: str = "alice@example.com", password: str = "hunter2ok"
) -> dict:
    resp = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(
    client: TestClient, email: str = "alice@example.com", password: str = "hunter2ok"
) -> dict:
    resp = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---- 注册与登录 ----


def test_register_creates_owner(client: TestClient) -> None:
    """注册即建组织:首个成员 owner,返回双令牌。"""
    body = _register(client)
    assert body["role"] == "owner"
    assert body["organization_id"] == body["tenant_id"]
    assert body["access_token"].count(".") == 2  # JWS 三段
    assert len(body["refresh_token"]) >= 40


def test_register_duplicate_email(client: TestClient) -> None:
    """同邮箱再注册是 409,不覆盖既有账号。"""
    _register(client)
    resp = client.post(
        "/v1/auth/register",
        json={"email": "alice@example.com", "password": "another-pass"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "account.email_taken"


def test_register_short_password_rejected(client: TestClient) -> None:
    """密码最小 8 位:更短的直接 422。"""
    resp = client.post(
        "/v1/auth/register", json={"email": "a@b.co", "password": "short"}
    )
    assert resp.status_code == 422


def test_login_returns_tokens(client: TestClient) -> None:
    body = _register(client)
    again = _login(client)
    assert again["user_id"] == body["user_id"]
    assert again["session_id"] != body["session_id"]  # 每次登录新会话


def test_login_failures_do_not_leak(client: TestClient) -> None:
    """密码错与邮箱不存在:同一错误码与文案(防枚举)。"""
    _register(client)
    wrong = client.post(
        "/v1/auth/login", json={"email": "alice@example.com", "password": "wrong!!"}
    )
    unknown = client.post(
        "/v1/auth/login", json={"email": "ghost@example.com", "password": "whatever1"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


# ---- 刷新:旋转 + 重用检测 ----


def test_refresh_rotation_single_use(client: TestClient) -> None:
    """刷新后旧令牌立即作废;重用会连带吊销新令牌(泄露防御)。"""
    body = _register(client)
    rotated = client.post(
        "/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rotated.status_code == 200
    new_pair = rotated.json()

    old = client.post("/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert old.status_code == 401
    assert old.json()["code"] == "auth.refresh_reused"

    # 重用是泄露信号:新签的令牌也被一并吊销
    chained = client.post(
        "/v1/auth/refresh", json={"refresh_token": new_pair["refresh_token"]}
    )
    assert chained.status_code == 401
    assert chained.json()["code"] == "auth.refresh_reused"


def test_refresh_unknown_token(client: TestClient) -> None:
    resp = client.post("/v1/auth/refresh", json={"refresh_token": "x" * 48})
    assert resp.status_code == 401
    assert resp.json()["code"] == "auth.refresh_invalid"


def test_logout_revokes_refresh(client: TestClient) -> None:
    """登出后刷新被拒;登出对未知令牌幂等(204)。"""
    body = _register(client)
    out = client.post("/v1/auth/logout", json={"refresh_token": body["refresh_token"]})
    assert out.status_code == 204
    assert (
        client.post(
            "/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
        ).status_code
        == 401
    )
    assert (
        client.post("/v1/auth/logout", json={"refresh_token": "y" * 48}).status_code
        == 204
    )


# ---- OIDC 与验签闭环(与托管 IdP 同构) ----


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
    """生产验证路径闭环:JwtVerifier 经发现文档 + JWKS 验签。"""
    body = _register(client)
    verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        transport=httpx.ASGITransport(app=app),
    )
    principal = await verifier.verify(body["access_token"])
    assert principal.subject == "alice@example.com"
    assert principal.tenant_id == body["tenant_id"]
    await verifier.aclose()


async def test_verifier_rejects_expired(app: FastAPI) -> None:
    """过期 token 必须被验签拒绝(401),无任何后门。"""
    stale = IdPSigner(issuer=ISSUER, audience=AUDIENCE, ttl_seconds=-10)
    token = stale.sign(subject="alice@example.com", tenant_id="t-x")
    verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        transport=httpx.ASGITransport(app=app),
    )
    with pytest.raises(UnauthorizedError):
        await verifier.verify(token)
    await verifier.aclose()


async def test_tenant_isolation(client: TestClient, app: FastAPI) -> None:
    """两个注册的 token:租户各自正确,互相不可冒充。"""
    alpha = _register(client, email="alpha@example.com")
    beta = _register(client, email="beta@example.com")
    verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        transport=httpx.ASGITransport(app=app),
    )
    assert (await verifier.verify(alpha["access_token"])).tenant_id == alpha[
        "tenant_id"
    ]
    assert (await verifier.verify(beta["access_token"])).tenant_id == beta["tenant_id"]
    assert alpha["tenant_id"] != beta["tenant_id"]
    await verifier.aclose()


def test_signing_key_persistence(tmp_path: Path) -> None:
    """signing_key_path 持久化:两次启动 kid 不变(不轮换)。"""
    key_path = tmp_path / "keys" / "signing.pem"
    first = create_app(
        _auth_settings(tmp_path / "a.db", signing_key_path=str(key_path))
    )
    second = create_app(
        _auth_settings(tmp_path / "b.db", signing_key_path=str(key_path))
    )
    with TestClient(first) as c1, TestClient(second) as c2:
        kid1 = c1.get("/jwks").json()["keys"][0]["kid"]
        kid2 = c2.get("/jwks").json()["keys"][0]["kid"]
    assert kid1 == kid2


# ---- 内部签发(服务间调用) ----


def _issue(client: TestClient, tenant: str, secret: str | None):
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
    secret = "internal-secret-1"
    app = create_app(_auth_settings(tmp_path / "issue.db", internal_secret=secret))
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
