"""附件本地最小版:上传/列表/下载/删除、类型与大小限制、消息绑定。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import _IDP, OTHER, TENANT, _settings


def _conversation(client: TestClient) -> str:
    response = client.post("/api/v1/conversations", json={}, headers=TENANT)
    assert response.status_code == 201
    return response.json()["id"]


def _upload(
    client: TestClient,
    conversation_id: str,
    filename: str = "note.md",
    content: bytes = b"# hello",
    content_type: str = "text/markdown",
):
    return client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": (filename, content, content_type)},
        headers=TENANT,
    )


def test_upload_list_download_delete(client: TestClient) -> None:
    conversation_id = _conversation(client)

    uploaded = _upload(client, conversation_id)
    assert uploaded.status_code == 201
    attachment = uploaded.json()
    assert attachment["filename"] == "note.md"
    assert attachment["content_type"] == "text/markdown"
    assert attachment["size_bytes"] == len(b"# hello")
    assert attachment["message_id"] is None

    listing = client.get(
        f"/api/v1/conversations/{conversation_id}/attachments", headers=TENANT
    )
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [attachment["id"]]

    downloaded = client.get(
        f"/api/v1/attachments/{attachment['id']}", headers=TENANT
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"# hello"
    assert downloaded.headers["content-type"].startswith("text/markdown")
    assert "attachment" in downloaded.headers["content-disposition"]

    deleted = client.delete(
        f"/api/v1/attachments/{attachment['id']}", headers=TENANT
    )
    assert deleted.status_code == 204
    missing = client.get(
        f"/api/v1/attachments/{attachment['id']}", headers=TENANT
    )
    assert missing.status_code == 404


def test_rejects_unsupported_type(client: TestClient) -> None:
    conversation_id = _conversation(client)
    response = _upload(
        client,
        conversation_id,
        filename="page.html",
        content=b"<script>alert(1)</script>",
        content_type="text/html",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "attachment.type_not_allowed"


def test_rejects_too_large(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path, "attachment-limit.db", max_attachment_bytes=4),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        conversation_id = _conversation(client)
        response = _upload(client, conversation_id, content=b"12345")
    assert response.status_code == 400
    assert response.json()["code"] == "attachment.too_large"


def test_message_binds_attachment_and_cannot_reuse(client: TestClient) -> None:
    conversation_id = _conversation(client)
    attachment_id = _upload(client, conversation_id).json()["id"]

    posted = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "整理这个附件", "attachment_ids": [attachment_id]},
        headers=TENANT,
    )
    assert posted.status_code == 201
    listing = client.get(
        f"/api/v1/conversations/{conversation_id}/attachments", headers=TENANT
    ).json()
    assert listing[0]["message_id"] == posted.json()["message"]["id"]

    reused = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "再发一次", "attachment_ids": [attachment_id]},
        headers=TENANT,
    )
    assert reused.status_code == 400
    assert reused.json()["code"] == "attachment.already_bound"


def test_attachment_tenant_isolation(client: TestClient) -> None:
    conversation_id = _conversation(client)
    attachment_id = _upload(client, conversation_id).json()["id"]

    assert (
        client.get(
            f"/api/v1/attachments/{attachment_id}", headers=OTHER
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/conversations/{conversation_id}/attachments", headers=OTHER
        ).status_code
        == 404
    )


def test_docx_attachment_extracted_and_injected(tmp_path: Path) -> None:
    import sqlite3
    from io import BytesIO

    from docx import Document

    document = Document()
    document.add_paragraph('项目周报:本周完成附件解析。')
    buffer = BytesIO()
    document.save(buffer)

    app = create_app(
        _settings(tmp_path, 'attachment-parse.db'),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        conversation_id = _conversation(client)
        uploaded = _upload(
            client,
            conversation_id,
            filename='weekly.docx',
            content=buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        assert uploaded.status_code == 201
        attachment_id = uploaded.json()['id']

        conn = sqlite3.connect(tmp_path / 'attachment-parse.db')
        extracted = conn.execute(
            'select extracted_text, extraction_error from attachments'
        ).fetchone()
        conn.close()
        assert extracted[1] is None
        assert '项目周报' in extracted[0]

        posted = client.post(
            f'/api/v1/conversations/{conversation_id}/messages',
            json={
                'content': '总结这个附件',
                'mode': 'task',
                'attachment_ids': [attachment_id],
            },
            headers=TENANT,
        )
        assert posted.status_code == 201
        assert '附件内容' in posted.json()['run']['goal']
