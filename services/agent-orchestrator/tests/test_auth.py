"""鉴权(架构 §10):Bearer token 换租户;伪造请求头无效。"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from common.auth import JwtVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import _settings

ISSUER = "https://idp.example.com/oidc"
KID = "key-1"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(private_key) -> dict:
    document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    document.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return {"keys": [document]}


def _token(private_key, tenant: str | None = "t-1", expires_in: int = 300) -> str:
    claims = {
        "sub": "u1",
        "iss": ISSUER,
        "exp": datetime.now(UTC) + timedelta(seconds=expires_in),
    }
    if tenant is not None:
        claims["tenant_id"] = tenant
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KID})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _auth_client(tmp_path, jwks) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jwks"):
            return httpx.Response(200, json=jwks)
        return httpx.Response(404)

    verifier = JwtVerifier(
        issuer=ISSUER,
        jwks_url=f"{ISSUER}/jwks",
        transport=httpx.MockTransport(handler),
    )
    app = create_app(
        _settings(tmp_path, "auth.db", auth_enabled=True), token_verifier=verifier
    )
    return TestClient(app)


def test_missing_token_rejected(tmp_path, jwks) -> None:
    with _auth_client(tmp_path, jwks) as client:
        resp = client.get("/api/v1/runs")
        assert resp.status_code == 401
        assert resp.json()["code"] == "auth.token_missing"


def test_invalid_token_rejected(tmp_path, jwks) -> None:
    with _auth_client(tmp_path, jwks) as client:
        resp = client.get("/api/v1/runs", headers=_bearer("garbage"))
        assert resp.status_code == 401
        assert resp.json()["code"] == "auth.token_invalid"


def test_tenant_from_token_and_isolation(tmp_path, jwks, private_key) -> None:
    with _auth_client(tmp_path, jwks) as client:
        created = client.post(
            "/api/v1/runs",
            json={"title": "周报", "goal": "整理日程"},
            headers=_bearer(_token(private_key, tenant="t-1")),
        )
        assert created.status_code == 201
        run_id = created.json()["id"]

        same = client.get(
            f"/api/v1/runs/{run_id}", headers=_bearer(_token(private_key, tenant="t-1"))
        )
        assert same.status_code == 200

        other = client.get(
            f"/api/v1/runs/{run_id}", headers=_bearer(_token(private_key, tenant="t-2"))
        )
        assert other.status_code == 404

        # 关键:鉴权开启时伪造 X-Tenant-Id 不生效
        spoofed = client.get(
            f"/api/v1/runs/{run_id}",
            headers={
                **_bearer(_token(private_key, tenant="t-2")),
                "X-Tenant-Id": "t-1",
            },
        )
        assert spoofed.status_code == 404


def test_token_without_tenant_claim_rejected(tmp_path, jwks, private_key) -> None:
    with _auth_client(tmp_path, jwks) as client:
        resp = client.get(
            "/api/v1/runs", headers=_bearer(_token(private_key, tenant=None))
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "auth.tenant_missing"
