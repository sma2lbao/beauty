"""交付物(§7):内容落对象存储、DB 只留元数据、读数一致、删除清对象。"""

from __future__ import annotations

import base64
from urllib.parse import quote

import pytest
from common.artifacts import build_artifact_store
from common.errors import StorageError
from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, _create_artifact, _settings, verifier
from workspace.main import create_app

CONTENT = "# 周报\n\n- 本周要点\n"


def test_artifact_content_goes_to_object_store(client: TestClient, tmp_path) -> None:
    created = _create_artifact(client, content=CONTENT, run_id="run-1")
    assert created.status_code == 201
    body = created.json()
    assert body["content"] == CONTENT
    assert body["storage"] == "filesystem"
    assert body["size_bytes"] == len(CONTENT.encode())
    assert body["workspace_id"]  # 落默认工作区

    # 对象真的落盘(<租户>/<工作区>/<交付物>.md)
    root = tmp_path / "objects"
    files = [path for path in root.rglob("*") if path.is_file()]
    assert len(files) == 1
    assert files[0].read_text() == CONTENT
    parts = files[0].relative_to(root).parts
    assert parts[0] == "t-1"
    assert parts[1] == body["workspace_id"]


def test_list_and_read_artifact(client: TestClient) -> None:
    artifact = _create_artifact(client).json()

    listed = client.get(
        f"/v1/workspaces/{artifact['workspace_id']}/artifacts", headers=TENANT
    ).json()
    assert [item["id"] for item in listed] == [artifact["id"]]
    assert "content" not in listed[0]  # 列表只给元数据

    fetched = client.get(f"/v1/artifacts/{artifact['id']}", headers=TENANT).json()
    assert fetched["title"] == "周报"
    assert fetched["content"] == "# 周报\n要点"


def test_delete_removes_object_and_metadata(client: TestClient, tmp_path) -> None:
    artifact = _create_artifact(client).json()

    assert (
        client.delete(f"/v1/artifacts/{artifact['id']}", headers=TENANT).status_code
        == 204
    )
    assert (
        client.get(f"/v1/artifacts/{artifact['id']}", headers=TENANT).status_code == 404
    )
    root = tmp_path / "objects"
    assert [path for path in root.rglob("*") if path.is_file()] == []


def test_artifact_isolation_and_missing(client: TestClient) -> None:
    artifact = _create_artifact(client).json()
    assert (
        client.get(f"/v1/artifacts/{artifact['id']}", headers=OTHER).status_code == 404
    )


def test_database_backend_keeps_content_in_row(tmp_path) -> None:
    """ARTIFACT_STORE=database:内容留在行里,不产生对象文件。"""
    settings = _settings(tmp_path, "db.db", artifact_store="database")
    with TestClient(create_app(settings, token_verifier=verifier())) as client:
        body = _create_artifact(client).json()
        assert body["storage"] == "database"
        root = tmp_path / "objects"
        assert not [path for path in root.rglob("*") if path.is_file()]


def test_unknown_backend_rejected() -> None:
    with pytest.raises(StorageError) as exc_info:
        build_artifact_store("cos-not-yet")
    assert exc_info.value.code == "artifact.unknown_store"


def test_missing_object_is_500(client: TestClient, tmp_path) -> None:
    """对象被外部删除:读取报 500(内部存储问题,不是 404)。"""
    artifact = _create_artifact(client).json()
    for path in (tmp_path / "objects").rglob("*"):
        if path.is_file():
            path.unlink()

    response = client.get(f"/v1/artifacts/{artifact['id']}", headers=TENANT)
    assert response.status_code == 500
    assert response.json()["code"] == "artifact.object_missing"


# ---- 二进制交付物与下载端点(交付物多件化) ----

BINARY = b"\x89PNG\r\n\x1a\n" + bytes(range(16))
BINARY_B64 = base64.b64encode(BINARY).decode("ascii")


def _create_binary(
    client: TestClient,
    title: str = "截图",
    filename: str | None = "screenshot.png",
    content_type: str | None = "image/png",
    **extra,
):
    payload = {"title": title, "content_base64": BINARY_B64, **extra}
    if filename is not None:
        payload["filename"] = filename
    if content_type is not None:
        payload["content_type"] = content_type
    return client.post("/v1/artifacts", json=payload, headers=TENANT)


def test_binary_artifact_metadata_and_download(client: TestClient, tmp_path) -> None:
    created = _create_binary(client)
    assert created.status_code == 201
    body = created.json()
    assert body["content"] is None  # 二进制没有文本正文
    assert body["filename"] == "screenshot.png"
    assert body["content_type"] == "image/png"
    assert body["size_bytes"] == len(BINARY)
    assert body["storage"] == "filesystem"

    # 对象键按 filename 的扩展名落盘
    files = [path for path in (tmp_path / "objects").rglob("*") if path.is_file()]
    assert len(files) == 1
    assert files[0].suffix == ".png"
    assert files[0].read_bytes() == BINARY

    # 列表/详情带元数据且不含二进制正文
    listed = client.get(
        f"/v1/workspaces/{body['workspace_id']}/artifacts", headers=TENANT
    ).json()
    assert listed[0]["filename"] == "screenshot.png"
    assert listed[0]["content_type"] == "image/png"
    assert "content" not in listed[0]

    fetched = client.get(f"/v1/artifacts/{body['id']}", headers=TENANT).json()
    assert fetched["filename"] == "screenshot.png"
    assert fetched["content_type"] == "image/png"
    assert fetched["content"] is None

    # 下载:原始字节 + Content-Type + RFC 5987 文件名 + 私有缓存
    download = client.get(f"/v1/artifacts/{body['id']}/download", headers=TENANT)
    assert download.status_code == 200
    assert download.headers["content-type"] == "image/png"
    assert download.content == BINARY
    assert (
        download.headers["content-disposition"]
        == "attachment; filename*=UTF-8''screenshot.png"
    )
    assert download.headers["cache-control"] == "private, no-store"


def test_download_defaults_and_utf8_filename(client: TestClient) -> None:
    """未给 filename/content_type:二进制默认 octet-stream,文件名按 UTF-8 编码。"""
    binary = _create_binary(client, title="图表", filename=None, content_type=None)
    assert binary.status_code == 201
    body = binary.json()
    assert body["filename"] == "图表.bin"
    assert body["content_type"] == "application/octet-stream"

    download = client.get(f"/v1/artifacts/{body['id']}/download", headers=TENANT)
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/octet-stream"
    assert quote("图表.bin") in download.headers["content-disposition"]

    # 文本不传 content_type:默认 text/markdown + title.md,下载内容是文本字节
    text = _create_artifact(client, title="说明").json()
    assert text["filename"] == "说明.md"
    assert text["content_type"] == "text/markdown"
    text_download = client.get(f"/v1/artifacts/{text['id']}/download", headers=TENANT)
    assert text_download.status_code == 200
    # Starlette 对 text/* 自动补 charset,断言前缀即可
    assert text_download.headers["content-type"].startswith("text/markdown")
    assert text_download.content == "# 周报\n要点".encode()


def test_binary_artifact_on_database_backend(tmp_path) -> None:
    """ARTIFACT_STORE=database:二进制落 content_bytes 列,下载仍拿到原始字节。"""
    settings = _settings(tmp_path, "db.db", artifact_store="database")
    with TestClient(create_app(settings, token_verifier=verifier())) as client:
        body = _create_binary(client).json()
        assert body["storage"] == "database"
        assert body["content"] is None
        assert body["size_bytes"] == len(BINARY)
        assert not [
            path for path in (tmp_path / "objects").rglob("*") if path.is_file()
        ]

        download = client.get(f"/v1/artifacts/{body['id']}/download", headers=TENANT)
        assert download.status_code == 200
        assert download.content == BINARY
        # 文本与二进制在同后端各自落列,互不回归
        assert _create_artifact(client).json()["content"] == "# 周报\n要点"


def test_content_and_base64_are_mutually_exclusive(client: TestClient) -> None:
    both = client.post(
        "/v1/artifacts",
        json={"title": "两边都给", "content": "文本", "content_base64": BINARY_B64},
        headers=TENANT,
    )
    assert both.status_code == 422
    neither = client.post("/v1/artifacts", json={"title": "都不给"}, headers=TENANT)
    assert neither.status_code == 422


def test_invalid_base64_rejected(client: TestClient) -> None:
    broken = client.post(
        "/v1/artifacts",
        json={"title": "坏 base64", "content_base64": "%%%not-base64%%%"},
        headers=TENANT,
    )
    assert broken.status_code == 422
    empty = client.post(
        "/v1/artifacts",
        json={"title": "空 base64", "content_base64": ""},
        headers=TENANT,
    )
    assert empty.status_code == 422


def test_download_is_tenant_isolated(client: TestClient) -> None:
    artifact = _create_binary(client).json()
    response = client.get(f"/v1/artifacts/{artifact['id']}/download", headers=OTHER)
    assert response.status_code == 404
