"""鉴权(架构 §10):connector-service 与 orchestrator 同一套 token→租户约定。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from common.auth import JwtVerifier
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from connector.core.config import Settings
from connector.main import create_app
from tests.conftest import insert_connection

ISSUER = "https://idp.example.com/oidc"
KID = "key-1"
AUTH_DB = "auth.db"


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


def _auth_client(tmp_path: Path, private_key) -> TestClient:
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
        database_url=f"sqlite+aiosqlite:///{tmp_path}/{AUTH_DB}",
        auto_create_tables=True,
        auth_enabled=True,
    )
    app = create_app(settings, token_verifier=verifier)
    return TestClient(app)


def test_connection_requires_token(tmp_path: Path, private_key) -> None:
    with _auth_client(tmp_path, private_key) as client:
        resp = client.get("/v1/connections")
        assert resp.status_code == 401
        assert resp.json()["code"] == "auth.token_missing"


def test_connection_scoped_to_token_tenant(tmp_path: Path, private_key) -> None:
    with _auth_client(tmp_path, private_key) as client:
        conn = insert_connection(tmp_path / AUTH_DB, tenant_id="t-1")
        headers = {"Authorization": f"Bearer {_token(private_key, tenant='t-1')}"}
        assert [
            row["id"] for row in client.get("/v1/connections", headers=headers).json()
        ] == [conn["id"]]

        other = {
            "Authorization": f"Bearer {_token(private_key, tenant='t-2')}",
            "X-Tenant-Id": "t-1",  # 鉴权开启时伪造请求头无效
        }
        assert client.get("/v1/connections", headers=other).json() == []
        assert (
            client.get(f"/v1/connections/{conn['id']}", headers=other).status_code
            == 404
        )
