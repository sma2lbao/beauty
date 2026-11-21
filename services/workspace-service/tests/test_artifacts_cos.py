"""COS 后端接入:新行记 cos、对象写入注入的 COS 客户端、下载/删除与对象 API。"""

from __future__ import annotations

import base64

import pytest
from common.cos_artifacts import CosArtifactConfig, CosArtifactStore
from common.errors import StorageError
from fastapi.testclient import TestClient
from qcloud_cos import CosServiceError

from tests.conftest import ACCOUNT, _create_artifact, _settings, verifier
from workspace.core.storage import build_store
from workspace.main import create_app

CONTENT = "# 周报\n\n- 本周要点\n"
BUCKET = "demo-1250000000"


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def get_raw_stream(self):
        import io

        return io.BytesIO(self._data)


class FakeCosClient:
    """内存版 COS:只实现 put/get/delete,供本地零网络测试注入。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.content_types: dict[tuple[str, str], str] = {}

    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str
    ) -> None:
        self.objects[(Bucket, Key)] = bytes(Body)
        self.content_types[(Bucket, Key)] = ContentType

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        try:
            return {"Body": _Body(self.objects[(Bucket, Key)])}
        except KeyError as exc:
            raise CosServiceError(
                "GET",
                {
                    "code": "NoSuchKey",
                    "message": "missing",
                    "requestid": "req-1",
                    "traceid": "trace-1",
                },
                404,
            ) from exc

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)


def _cos_store() -> tuple[CosArtifactStore, FakeCosClient]:
    client = FakeCosClient()
    config = CosArtifactConfig(
        bucket=BUCKET,
        region="ap-seoul",
        secret_id="test-id",
        secret_key="test-key",
    )
    return CosArtifactStore(config, client=client), client


def test_artifact_writes_to_cos_and_reads_back(tmp_path) -> None:
    store, fake = _cos_store()
    settings = _settings(tmp_path, "cos.db", artifact_store="cos")
    with TestClient(
        create_app(settings, artifact_store=store, token_verifier=verifier())
    ) as client:
        created = _create_artifact(client, content=CONTENT, run_id="run-1")
        assert created.status_code == 201
        body = created.json()
        assert body["storage"] == "cos"
        assert body["size_bytes"] == len(CONTENT.encode())
        assert body["content"] == CONTENT

        assert len(fake.objects) == 1
        (bucket, key), payload = next(iter(fake.objects.items()))
        assert bucket == BUCKET
        assert key.startswith("t-1/")
        assert key.endswith(".md")
        assert payload == CONTENT.encode()

        fetched = client.get(f"/v1/artifacts/{body['id']}", headers=ACCOUNT)
        assert fetched.status_code == 200
        assert fetched.json()["content"] == CONTENT

        download = client.get(f"/v1/artifacts/{body['id']}/download", headers=ACCOUNT)
        assert download.status_code == 200
        assert download.content == CONTENT.encode()

        deleted = client.delete(f"/v1/artifacts/{body['id']}", headers=ACCOUNT)
        assert deleted.status_code == 204
        assert fake.objects == {}


def test_binary_artifact_and_object_api_use_cos(tmp_path) -> None:
    store, fake = _cos_store()
    settings = _settings(tmp_path, "cos.db", artifact_store="cos")
    binary = b"\x89PNG\r\n\x1a\n" + bytes(range(16))
    with TestClient(
        create_app(settings, artifact_store=store, token_verifier=verifier())
    ) as client:
        artifact = client.post(
            "/v1/artifacts",
            json={
                "title": "截图",
                "content_base64": base64.b64encode(binary).decode("ascii"),
                "filename": "screenshot.png",
                "content_type": "image/png",
            },
            headers=ACCOUNT,
        )
        assert artifact.status_code == 201
        artifact_body = artifact.json()
        assert artifact_body["storage"] == "cos"
        artifact_key = next(key for (_, key) in fake.objects)
        assert artifact_key.endswith(".png")
        assert fake.objects[(BUCKET, artifact_key)] == binary

        created = client.post(
            "/v1/objects",
            json={
                "filename": "demo.skill.zip",
                "content_type": "application/zip",
                "content_base64": base64.b64encode(binary).decode("ascii"),
            },
            headers=ACCOUNT,
        )
        assert created.status_code == 201
        object_body = created.json()
        assert object_body["key"].startswith("t-1/objects/")
        assert fake.objects[(BUCKET, object_body["key"])] == binary

        fetched = client.get(
            "/v1/objects", params={"key": object_body["key"]}, headers=ACCOUNT
        )
        assert fetched.status_code == 200
        assert base64.b64decode(fetched.json()["content_base64"]) == binary

        assert (
            client.delete(
                "/v1/objects", params={"key": object_body["key"]}, headers=ACCOUNT
            ).status_code
            == 204
        )
        assert (BUCKET, object_body["key"]) not in fake.objects


def test_build_store_requires_cos_credentials(tmp_path) -> None:
    settings = _settings(tmp_path, "cos.db", artifact_store="cos")
    with pytest.raises(StorageError) as exc_info:
        build_store(settings)
    assert exc_info.value.code == "artifact.cos_config_invalid"
