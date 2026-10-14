"""交付物(§7):内容落对象存储、DB 只留元数据、读数一致、删除清对象。"""

from __future__ import annotations

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
