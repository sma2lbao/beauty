"""通用二进制对象(P3):skill 包往返、租户隔离、大小/编码校验、无存储后端。"""

from __future__ import annotations

import base64
import hashlib

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, _settings, verifier
from workspace.main import create_app

# 带 zip 魔数与 NUL/非 UTF-8 字节,确认二进制不被文本路径破坏
SKILL_ZIP = b"PK\x03\x04skill-package\x00\xff\xfe"


def _put(client: TestClient, payload: bytes = SKILL_ZIP, **body):
    return client.post(
        "/v1/objects",
        json={
            "filename": "demo.skill.zip",
            "content_type": "application/zip",
            "content_base64": base64.b64encode(payload).decode("ascii"),
            **body,
        },
        headers=TENANT,
    )


def test_put_get_roundtrip(client: TestClient, tmp_path) -> None:
    created = _put(client)
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("t-1/objects/")
    assert body["key"].endswith(".zip")
    assert body["size_bytes"] == len(SKILL_ZIP)
    assert body["sha256"] == hashlib.sha256(SKILL_ZIP).hexdigest()

    fetched = client.get("/v1/objects", params={"key": body["key"]}, headers=TENANT)
    assert fetched.status_code == 200
    read = fetched.json()
    assert read["key"] == body["key"]
    assert read["size_bytes"] == len(SKILL_ZIP)
    assert read["sha256"] == body["sha256"]
    assert base64.b64decode(read["content_base64"]) == SKILL_ZIP

    # 对象真的落盘在 <租户>/objects/ 下
    files = [path for path in (tmp_path / "objects").rglob("*") if path.is_file()]
    assert len(files) == 1
    assert files[0].read_bytes() == SKILL_ZIP
    parts = files[0].relative_to(tmp_path / "objects").parts
    assert parts[:2] == ("t-1", "objects")


def test_filename_only_contributes_safe_suffix(client: TestClient) -> None:
    key = _put(client, filename="../../evil.SKILL").json()["key"]
    assert key.startswith("t-1/objects/")
    assert key.endswith(".skill")
    assert "evil" not in key
    assert ".." not in key


def test_object_without_filename_has_no_suffix(client: TestClient) -> None:
    key = _put(client, filename=None).json()["key"]
    assert key.startswith("t-1/objects/")
    assert "." not in key.rsplit("/", 1)[-1]


def test_cross_tenant_read_and_delete_are_404(client: TestClient) -> None:
    key = _put(client).json()["key"]
    assert (
        client.get("/v1/objects", params={"key": key}, headers=OTHER).status_code == 404
    )
    assert (
        client.delete("/v1/objects", params={"key": key}, headers=OTHER).status_code
        == 404
    )
    # 主人仍能读:越权请求没有副作用
    owner = client.get("/v1/objects", params={"key": key}, headers=TENANT)
    assert owner.status_code == 200


def test_delete_is_scoped_and_idempotent(client: TestClient) -> None:
    key = _put(client).json()["key"]
    assert (
        client.delete("/v1/objects", params={"key": key}, headers=TENANT).status_code
        == 204
    )
    missing = client.get("/v1/objects", params={"key": key}, headers=TENANT)
    assert missing.status_code == 404
    # 已删除再删仍 204(幂等)
    assert (
        client.delete("/v1/objects", params={"key": key}, headers=TENANT).status_code
        == 204
    )


def test_missing_or_malformed_key_is_404(client: TestClient) -> None:
    missing = "t-1/objects/" + "0" * 32 + ".zip"
    assert (
        client.get("/v1/objects", params={"key": missing}, headers=TENANT).status_code
        == 404
    )
    for key in (
        "t-2/objects/" + "0" * 32 + ".zip",  # 别的租户
        "t-1/objects/../t-2/secret.zip",  # 路径穿越
        "t-1/objects/",  # 没有对象名
        "/etc/passwd",  # 绝对路径
    ):
        response = client.get("/v1/objects", params={"key": key}, headers=TENANT)
        assert response.status_code == 404
        assert response.json()["code"] == "object.not_found"


def test_invalid_base64_is_400(client: TestClient) -> None:
    response = client.post(
        "/v1/objects", json={"content_base64": "not-base64!!"}, headers=TENANT
    )
    assert response.status_code == 400
    assert response.json()["code"] == "object.content_invalid"


def test_oversized_object_is_400(tmp_path) -> None:
    settings = _settings(tmp_path, "small.db", object_max_bytes=8)
    with TestClient(create_app(settings, token_verifier=verifier())) as client:
        response = _put(client, payload=b"0123456789")
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "object.too_large"
        assert body["details"]["max_bytes"] == 8
        # 超限请求不留下任何对象文件
        files = [path for path in (tmp_path / "objects").rglob("*") if path.is_file()]
        assert files == []


def test_database_backend_has_no_object_store(tmp_path) -> None:
    """ARTIFACT_STORE=database 时 store=None:明确 503,不静默成功。"""
    settings = _settings(tmp_path, "db.db", artifact_store="database")
    with TestClient(create_app(settings, token_verifier=verifier())) as client:
        created = _put(client)
        assert created.status_code == 503
        assert created.json()["code"] == "object.store_missing"
        fetched = client.get(
            "/v1/objects", params={"key": "t-1/objects/x.zip"}, headers=TENANT
        )
        assert fetched.status_code == 503
        assert fetched.json()["code"] == "object.store_missing"
