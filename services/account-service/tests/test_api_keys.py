"""API Key(M3-01):只显示一次明文、哈希存储、轮换/吊销、内部校验。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from account.main import create_app
from tests.conftest import _IDP, OTHER, TENANT, _settings


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        _settings(tmp_path, internal_secret="internal-secret"),
        token_verifier=_IDP.verifier(),
    )
    return TestClient(app)


def _create(client: TestClient, tenant: dict | None = None) -> dict:
    response = client.post(
        "/v1/api-keys",
        json={"name": "mcp", "scopes": ["mcp:tools:read", "mcp:tools:call"]},
        headers=tenant or TENANT,
    )
    assert response.status_code == 201
    return response.json()


def test_create_returns_plaintext_once_and_hashes_db(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = _create(client)
        plaintext = created["key"]
        assert plaintext.startswith("bm_live_")
        assert created["masked_key"].endswith(created["key_hint"])
        assert "masked_key" in created

        listing = client.get("/v1/api-keys", headers=TENANT).json()
        assert listing[0]["id"] == created["id"]
        assert "key" not in listing[0]
        assert plaintext not in str(listing)

        conn = sqlite3.connect(tmp_path / "test.db")
        stored = conn.execute(
            "select key_hash, key_hint from api_keys"
        ).fetchone()
        conn.close()
        assert plaintext not in stored[0]
        assert len(stored[0]) == 64
        assert stored[1] == created["key_hint"]


def test_verify_rotate_and_revoke(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = _create(client)
        key = created["key"]

        verified = client.post(
            "/v1/api-keys/verify",
            json={"key": key},
            headers={"X-Internal-Secret": "internal-secret"},
        )
        assert verified.status_code == 200
        assert verified.json()["tenant_id"]
        assert "mcp:tools:call" in verified.json()["scopes"]

        rotated = client.post(
            f"/v1/api-keys/{created['id']}/rotate", headers=TENANT
        )
        assert rotated.status_code == 200
        new_key = rotated.json()["key"]
        assert new_key != key

        # 旧 key 已吊销
        old = client.post(
            "/v1/api-keys/verify",
            json={"key": key},
            headers={"X-Internal-Secret": "internal-secret"},
        )
        assert old.status_code == 401
        assert (
            client.post(
                "/v1/api-keys/verify",
                json={"key": new_key},
                headers={"X-Internal-Secret": "internal-secret"},
            ).status_code
            == 200
        )

        assert (
            client.delete(
                f"/v1/api-keys/{rotated.json()['id']}", headers=TENANT
            ).status_code
            == 204
        )
        assert (
            client.post(
                "/v1/api-keys/verify",
                json={"key": new_key},
                headers={"X-Internal-Secret": "internal-secret"},
            ).status_code
            == 401
        )


def test_internal_verify_requires_secret_and_tenant_isolation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = _create(client)
        wrong = client.post("/v1/api-keys/verify", json={"key": created["key"]})
        assert wrong.status_code == 401
        assert client.get("/v1/api-keys", headers=OTHER).json() == []
