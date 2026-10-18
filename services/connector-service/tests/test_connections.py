"""连接 CRUD:凭证只进不出、租户隔离、重复 409、撤销守卫、传输端点。"""

from pathlib import Path

from fastapi.testclient import TestClient

from connector.main import create_app
from connector.services.mail import EmailSummary
from tests.conftest import OTHER, TENANT, FakeTransport, _settings, verifier

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


def _create(client: TestClient, account: str = "me@qq.com", tenant: dict | None = None):
    return client.post(
        "/v1/connections",
        json={**BODY, "account": account},
        headers=tenant or TENANT,
    )


def test_create_returns_no_credentials(client: TestClient) -> None:
    """凭证只进不出:响应里绝不能有 credentials/password 字段。"""
    resp = _create(client)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "active"
    assert data["account"] == "me@qq.com"
    assert "credentials" not in data
    assert "password" not in str(data)


def test_duplicate_account_conflict(client: TestClient) -> None:
    assert _create(client).status_code == 201
    dup = _create(client)
    assert dup.status_code == 409
    assert dup.json()["code"] == "connector.connection_exists"


def test_provider_validation_phase_a(client: TestClient) -> None:
    """阶段 A 仅 imap:gmail 应 422(阶段 B 开放 OAuth 双轨)。"""
    resp = client.post(
        "/v1/connections",
        json={**BODY, "provider": "gmail"},
        headers=TENANT,
    )
    assert resp.status_code == 422


def test_non_ascii_credentials_rejected_at_schema(client: TestClient) -> None:
    """非 ASCII 凭证注册时 422 拦下(IMAP LOGIN 按 ASCII 编码),不留给探活炸。"""
    resp = client.post(
        "/v1/connections",
        json={
            **BODY,
            "credentials": {**BODY["credentials"], "password": "假授权码不是ASCII"},
        },
        headers=TENANT,
    )
    assert resp.status_code == 422


def test_tenant_isolation(client: TestClient) -> None:
    conn_id = _create(client).json()["id"]
    other = OTHER
    assert client.get("/v1/connections", headers=other).json() == []
    assert client.get(f"/v1/connections/{conn_id}", headers=other).status_code == 404


def test_revoke_and_guards(client: TestClient) -> None:
    conn_id = _create(client).json()["id"]
    revoked = client.delete(f"/v1/connections/{conn_id}", headers=TENANT).json()
    assert revoked["status"] == "revoked"
    # 重复撤销 409;撤销后 test/emails 都被守卫拦下
    assert (
        client.delete(f"/v1/connections/{conn_id}", headers=TENANT).status_code == 409
    )
    assert (
        client.post(f"/v1/connections/{conn_id}/test", headers=TENANT).status_code
        == 409
    )
    assert (
        client.get(f"/v1/connections/{conn_id}/emails", headers=TENANT).status_code
        == 409
    )


def test_unknown_connection_404(client: TestClient) -> None:
    for method, path in [
        ("get", "/v1/connections/nope"),
        ("delete", "/v1/connections/nope"),
        ("post", "/v1/connections/nope/test"),
        ("get", "/v1/connections/nope/emails"),
    ]:
        resp = getattr(client, method)(path, headers=TENANT)
        assert resp.status_code == 404
        assert resp.json()["code"] == "connector.connection_not_found"


def test_endpoint_updates_last_checked(client: TestClient) -> None:
    conn_id = _create(client).json()["id"]
    result = client.post(f"/v1/connections/{conn_id}/test", headers=TENANT)
    assert result.json() == {"ok": True, "detail": "假连接正常"}
    data = client.get(f"/v1/connections/{conn_id}", headers=TENANT).json()
    assert data["last_checked_at"] is not None


def test_emails_endpoint_and_limit(tmp_path: Path) -> None:
    emails = [
        EmailSummary(
            subject=f"邮件{i}",
            sender="a@b.c",
            sent_at="Mon, 12 Oct 2026",
            snippet="正文",
        )
        for i in range(3)
    ]
    transport = FakeTransport(emails=emails)
    app = create_app(
        _settings(tmp_path),
        transport_factory=lambda conn: transport,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        conn_id = _create(client).json()["id"]
        got = client.get(
            f"/v1/connections/{conn_id}/emails?limit=2", headers=TENANT
        ).json()
        assert [e["subject"] for e in got] == ["邮件0", "邮件1"]
        assert transport.fetch_limits == [2]
        assert got[0]["sender"] == "a@b.c"


def test_send_email_endpoint(tmp_path: Path) -> None:
    """发信(写操作):服务端执行,人工确认由 orchestrator 的确认闸负责。"""
    transport = FakeTransport()
    app = create_app(
        _settings(tmp_path),
        transport_factory=lambda conn: transport,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        conn_id = _create(client).json()["id"]
        resp = client.post(
            f"/v1/connections/{conn_id}/emails",
            json={"to": "boss@example.com", "subject": "周报", "body": "本周汇总"},
            headers=TENANT,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert transport.sent == [("boss@example.com", "周报", "本周汇总")]


def test_send_email_requires_active_connection(client: TestClient) -> None:
    conn_id = _create(client).json()["id"]
    client.delete(f"/v1/connections/{conn_id}", headers=TENANT)
    resp = client.post(
        f"/v1/connections/{conn_id}/emails",
        json={"to": "a@b.c", "subject": "s", "body": "b"},
        headers=TENANT,
    )
    assert resp.status_code == 409


def test_send_email_rejects_non_ascii_recipient(client: TestClient) -> None:
    conn_id = _create(client).json()["id"]
    resp = client.post(
        f"/v1/connections/{conn_id}/emails",
        json={"to": "张三@example.com", "subject": "s", "body": "b"},
        headers=TENANT,
    )
    assert resp.status_code == 422


def test_connection_not_found_details_key(client) -> None:
    """404 的 details 键名是 connection_id(由仓储 id_detail_key 声明,别改名)。"""
    response = client.get("/v1/connections/nope", headers=TENANT)
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "connector.connection_not_found"
    assert body["details"] == {"connection_id": "nope"}


def test_credentials_encrypted_at_rest(client: TestClient, tmp_path: Path) -> None:
    import json
    import sqlite3

    from connector.core.crypto import decrypt_credentials

    assert _create(client).status_code == 201
    conn = sqlite3.connect(tmp_path / "test.db")
    row = conn.execute("select credentials from connections").fetchone()
    conn.close()
    stored = json.loads(row[0])
    assert "password" not in str(stored)
    assert stored.get("ciphertext", "").startswith("fernet:")
    decrypted = decrypt_credentials(
        "dev-connector-secret-change-me", stored
    )
    assert decrypted["password"] == "fake-auth-code-16chars"


def test_attachments_list_and_download(tmp_path: Path) -> None:
    from connector.main import create_app
    from connector.services.mail import AttachmentInfo
    from tests.conftest import FakeTransport, _settings, verifier

    transport = FakeTransport()
    transport.attachment_list = [
        AttachmentInfo(
            id="a1", filename="note.txt", content_type="text/plain", size_bytes=4
        )
    ]
    app = create_app(
        _settings(tmp_path),
        transport_factory=lambda conn: transport,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        created = _create(client)
        cid = created.json()["id"]
        listing = client.get(
            f"/v1/connections/{cid}/emails/m1/attachments", headers=TENANT
        )
        assert listing.status_code == 200
        assert listing.json()[0]["filename"] == "note.txt"

        download = client.get(
            f"/v1/connections/{cid}/emails/m1/attachments/a1", headers=TENANT
        )
        assert download.status_code == 200
        assert download.content == b"attachment-bytes"
        assert "note.txt" in download.headers["content-disposition"]
