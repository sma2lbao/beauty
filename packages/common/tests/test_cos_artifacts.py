"""COS 后端:三动作语义、404 幂等、错误映射、配置与对象键校验(零网络)。"""

from __future__ import annotations

import io

import pytest
from qcloud_cos import CosClientError, CosServiceError

from common.artifacts import (
    COS_BACKEND,
    DEFAULT_CONTENT_TYPE,
    build_artifact_store,
)
from common.cos_artifacts import CosArtifactConfig, CosArtifactStore
from common.errors import StorageError

BUCKET = "demo-1250000000"
KEY = "t-1/run-1/a.md"


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def get_raw_stream(self) -> io.BytesIO:
        return io.BytesIO(self._data)


class FakeCosClient:
    """只实现三个用到的动作;可注入任意异常模拟服务端/网络失败。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.content_types: dict[tuple[str, str], str] = {}
        self.fail: Exception | None = None

    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str
    ) -> None:
        self._raise()
        self.objects[(Bucket, Key)] = bytes(Body)
        self.content_types[(Bucket, Key)] = ContentType

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        self._raise()
        try:
            return {"Body": _Body(self.objects[(Bucket, Key)])}
        except KeyError as exc:
            raise _not_found("GET") from exc

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self._raise()
        if (Bucket, Key) not in self.objects:
            raise _not_found("DELETE")
        del self.objects[(Bucket, Key)]

    def _raise(self) -> None:
        if self.fail is not None:
            raise self.fail


def _not_found(method: str) -> CosServiceError:
    return CosServiceError(
        method,
        {
            "code": "NoSuchKey",
            "message": "missing",
            "requestid": "req-1",
            "traceid": "trace-1",
        },
        404,
    )


def _config(**overrides) -> CosArtifactConfig:
    values = {
        "bucket": BUCKET,
        "region": "ap-seoul",
        "secret_id": "test-id",
        "secret_key": "test-key",
    }
    values.update(overrides)
    return CosArtifactConfig(**values)


def _store(fake: FakeCosClient | None = None) -> tuple[CosArtifactStore, FakeCosClient]:
    client = fake or FakeCosClient()
    return CosArtifactStore(_config(), client=client), client


async def test_put_get_delete_roundtrip() -> None:
    store, client = _store()
    assert store.backend == COS_BACKEND

    size = await store.put(KEY, "# 周报\n".encode(), content_type="text/markdown")
    assert size == len("# 周报\n".encode())
    assert client.objects[(BUCKET, KEY)] == "# 周报\n".encode()
    assert client.content_types[(BUCKET, KEY)] == "text/markdown"
    assert await store.get(KEY) == "# 周报\n".encode()

    await store.delete(KEY)
    assert client.objects == {}
    # 删除不存在的对象是幂等的
    await store.delete(KEY)


async def test_get_missing_returns_none() -> None:
    store, client = _store()
    assert await store.get(KEY) is None
    assert client.objects == {}


async def test_put_returns_payload_size_for_byteslike() -> None:
    store, _ = _store()
    assert await store.put(KEY, bytearray(b"12345")) == 5


async def test_service_error_maps_to_storage_error() -> None:
    store, client = _store()
    client.fail = CosServiceError(
        "PUT",
        {
            "code": "InternalError",
            "message": "boom",
            "requestid": "req-2",
            "traceid": "trace-2",
        },
        500,
    )
    with pytest.raises(StorageError) as exc_info:
        await store.put(KEY, b"x")
    error = exc_info.value
    assert error.code == "artifact.cos_put_failed"
    assert error.status == 500
    assert error.details["status_code"] == 500
    assert error.details["error_code"] == "InternalError"
    assert error.details["request_id"] == "req-2"
    assert "test-key" not in str(error.details)


async def test_client_error_maps_to_storage_error() -> None:
    store, client = _store()
    client.fail = CosClientError("timeout")
    with pytest.raises(StorageError) as exc_info:
        await store.get(KEY)
    error = exc_info.value
    assert error.code == "artifact.cos_get_failed"
    assert error.details["error_type"] == "CosClientError"
    assert "test-key" not in str(error.details)


async def test_content_type_is_sanitized() -> None:
    store, client = _store()
    await store.put(KEY, b"x", content_type="")
    assert client.content_types[(BUCKET, KEY)] == DEFAULT_CONTENT_TYPE

    await store.put(KEY, b"x", content_type="text/plain\r\nX-Evil: 1")
    assert client.content_types[(BUCKET, KEY)] == DEFAULT_CONTENT_TYPE


@pytest.mark.parametrize(
    "key",
    ["", "/abs.md", "a//b.md", "a/../b.md", "a b.md", "a/" + "x" * 512],
)
async def test_invalid_key_rejected(key: str) -> None:
    store, _ = _store()
    with pytest.raises(StorageError) as exc_info:
        await store.put(key, b"x")
    assert exc_info.value.code == "artifact.invalid_key"


@pytest.mark.parametrize(
    "overrides",
    [
        {"bucket": ""},
        {"region": ""},
        {"secret_id": ""},
        {"secret_key": ""},
        {"scheme": "ftp"},
        {"endpoint": "https://cos.ap-seoul.myqcloud.com"},
        {"endpoint": "cos.ap-seoul.myqcloud.com/path"},
        {"timeout_seconds": 0},
        {"retry_times": -1},
        {"pool_max_size": 0},
    ],
)
def test_invalid_config_rejected(overrides: dict) -> None:
    with pytest.raises(StorageError) as exc_info:
        CosArtifactStore(_config(**overrides), client=FakeCosClient())
    assert exc_info.value.code == "artifact.cos_config_invalid"


def test_factory_requires_cos_config() -> None:
    with pytest.raises(StorageError) as exc_info:
        build_artifact_store(COS_BACKEND)
    assert exc_info.value.code == "artifact.cos_config_missing"


def test_factory_builds_cos_store() -> None:
    store = build_artifact_store(COS_BACKEND, cos=_config())
    assert store is not None
    assert store.backend == COS_BACKEND
