"""连接读端点:列表/详情/撤销、凭证只进不出、租户隔离与状态守卫。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, insert_connection

TEST_DB = "test.db"


def test_list_and_detail(client: TestClient, tmp_path: Path) -> None:
    active = insert_connection(tmp_path / TEST_DB, provider="gmail")
    legacy = insert_connection(
        tmp_path / TEST_DB, provider="imap", account="legacy@example.com"
    )

    listing = client.get("/v1/connections", headers=TENANT)
    assert listing.status_code == 200
    rows = listing.json()
    assert {row["id"] for row in rows} == {active["id"], legacy["id"]}
    # 凭证只进不出:列表与详情都不含 credentials/ciphertext/refresh_token
    assert all("credentials" not in row for row in rows)
    assert "refresh_token" not in listing.text
    assert "ciphertext" not in listing.text

    detail = client.get(f"/v1/connections/{active['id']}", headers=TENANT)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["provider"] == "gmail"
    assert payload["account"] == "me@gmail.com"
    assert payload["status"] == "active"
    assert "credentials" not in payload


def test_tenant_isolation(client: TestClient, tmp_path: Path) -> None:
    foreign = insert_connection(
        tmp_path / TEST_DB, tenant_id="t-2", account="other@example.com"
    )
    assert client.get("/v1/connections", headers=TENANT).json() == []
    assert (
        client.get(f"/v1/connections/{foreign['id']}", headers=TENANT).status_code
        == 404
    )
    assert [
        row["id"] for row in client.get("/v1/connections", headers=OTHER).json()
    ] == [foreign["id"]]


def test_revoke_and_guards(client: TestClient, tmp_path: Path) -> None:
    conn = insert_connection(tmp_path / TEST_DB)
    revoked = client.delete(f"/v1/connections/{conn['id']}", headers=TENANT).json()
    assert revoked["status"] == "revoked"
    # 撤销是留痕不是物理删除:仍可被列出
    assert [
        row["id"] for row in client.get("/v1/connections", headers=TENANT).json()
    ] == [conn["id"]]

    # 重复撤销 → 409(状态守卫)
    again = client.delete(f"/v1/connections/{conn['id']}", headers=TENANT)
    assert again.status_code == 409
    assert again.json()["code"] == "connector.invalid_state"

    # 非 active(如历史 expired)不可撤销
    expired = insert_connection(
        tmp_path / TEST_DB, account="expired@example.com", status="expired"
    )
    blocked = client.delete(f"/v1/connections/{expired['id']}", headers=TENANT)
    assert blocked.status_code == 409
    assert (
        client.get(f"/v1/connections/{expired['id']}", headers=TENANT).json()["status"]
        == "expired"
    )


def test_unknown_connection_404(client: TestClient) -> None:
    for method in ("get", "delete"):
        response = getattr(client, method)("/v1/connections/nope", headers=TENANT)
        assert response.status_code == 404
        assert response.json()["code"] == "connector.connection_not_found"
        assert response.json()["details"] == {"connection_id": "nope"}
