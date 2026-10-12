"""鉴权(架构 §10):connector-service 与 orchestrator 同一套 token→租户约定。"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from common.auth import JwtVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from connector.core.config import Settings
from connector.main import create_app
from tests.conftest import FakeTransport

ISSUER = "https://idp.example.com/oidc"
KID = "key-1"

BODY = {
    "provider": "imap",
    "account": "me@qq.com",
    "credentials": {
        "host": "imap.qq.com",
        "port": 993,
        "username": "me@qq.com",
        "password": "fake-auth-code-16chars",
    },
}


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(private_key, tenant: str = "t-1") -> str:
    return jwt.encode(
        {
            "sub": "u1",
            "iss": ISSUER,
            "tenant_id": tenant,
            "exp": datetime.now(UTC) + timedelta(seconds=300),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": KID},
    )


def _auth_client(tmp_path, private_key) -> TestClient:
    document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    document.update({"kid": KID, "alg": "RS256", "use": "sig"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [document]})
        return httpx.Response(404)

    verifier = JwtVerifier(
        issuer=ISSUER,
        jwks_url=f"{ISSUER}/jwks",
        transport=httpx.MockTransport(handler),
    )
    settings = Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/auth.db",
        auto_create_tables=True,
        auth_enabled=True,
    )
    app = create_app(
        settings,
        transport_factory=lambda conn: FakeTransport(),
        token_verifier=verifier,
    )
    return TestClient(app)


def test_connection_requires_token(tmp_path, private_key) -> None:
    with _auth_client(tmp_path, private_key) as client:
        resp = client.post("/v1/connections", json=BODY)
        assert resp.status_code == 401
        assert resp.json()["code"] == "auth.token_missing"


def test_connection_scoped_to_token_tenant(tmp_path, private_key) -> None:
    with _auth_client(tmp_path, private_key) as client:
        headers = {"Authorization": f"Bearer {_token(private_key, tenant='t-1')}"}
        created = client.post("/v1/connections", json=BODY, headers=headers)
        assert created.status_code == 201
        connection_id = created.json()["id"]

        other = {
            "Authorization": f"Bearer {_token(private_key, tenant='t-2')}",
            "X-Tenant-Id": "t-1",  # 鉴权开启时伪造请求头无效
        }
        assert (
            client.get(f"/v1/connections/{connection_id}", headers=other).status_code
            == 404
        )
