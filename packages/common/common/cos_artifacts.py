"""腾讯云 COS 交付物存储(生产后端)。

实现 `common.artifacts.ArtifactStore` 协议:同步 SDK 调用统一走
`asyncio.to_thread`,不阻塞事件循环;key 由调用方生成,这里只做二次安全校验。

约定:

- COS 桶按环境独立,不额外加 prefix;`object_key` 就是桶内真实 key;
- 404 `get` 返回 None、404 `delete` 视为成功(幂等);
- 其余 COS/网络错误统一映射为 `StorageError`(500),details 只带
  bucket/key/status/request_id,不带 SecretId/SecretKey;
- 同步 SDK 自带重试,这里不再叠加应用层重试。
"""

from __future__ import annotations

import re
from asyncio import to_thread
from dataclasses import dataclass
from typing import Any

from qcloud_cos import CosClientError, CosConfig, CosS3Client, CosServiceError

from common.artifacts import COS_BACKEND, DEFAULT_CONTENT_TYPE
from common.errors import StorageError

_SAFE_PART = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_KEY_LENGTH = 512
_MAX_CONTENT_TYPE_LENGTH = 128


@dataclass(frozen=True, slots=True)
class CosArtifactConfig:
    """COS 后端配置;由服务层从 Settings 组装,不在 common 里读环境变量。"""

    bucket: str
    region: str
    secret_id: str
    secret_key: str
    token: str = ""
    scheme: str = "https"
    endpoint: str = ""
    timeout_seconds: float = 30.0
    retry_times: int = 3
    pool_max_size: int = 10


class CosArtifactStore:
    """腾讯云 COS 实现:put/get/delete 三动作,行为与 filesystem 后端一致。"""

    backend = COS_BACKEND

    def __init__(
        self,
        config: CosArtifactConfig,
        *,
        client: Any | None = None,
    ) -> None:
        _validate_config(config)
        self._bucket = config.bucket.strip()
        self._client = client or _build_client(config)

    async def put(
        self, key: str, data: bytes, *, content_type: str = DEFAULT_CONTENT_TYPE
    ) -> int:
        _validate_key(key)
        payload = bytes(data)
        media_type = _clean_content_type(content_type)
        try:
            await to_thread(self._put_sync, key, payload, media_type)
        except CosServiceError as exc:
            raise _service_error("put", self._bucket, key, exc) from exc
        except CosClientError as exc:
            raise _client_error("put", self._bucket, key, exc) from exc
        return len(payload)

    async def get(self, key: str) -> bytes | None:
        _validate_key(key)
        try:
            return await to_thread(self._get_sync, key)
        except CosServiceError as exc:
            if _status_code(exc) == 404:
                return None
            raise _service_error("get", self._bucket, key, exc) from exc
        except CosClientError as exc:
            raise _client_error("get", self._bucket, key, exc) from exc

    async def delete(self, key: str) -> None:
        _validate_key(key)
        try:
            await to_thread(self._delete_sync, key)
        except CosServiceError as exc:
            if _status_code(exc) == 404:
                return
            raise _service_error("delete", self._bucket, key, exc) from exc
        except CosClientError as exc:
            raise _client_error("delete", self._bucket, key, exc) from exc

    def _put_sync(self, key: str, payload: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Body=payload,
            Key=key,
            ContentType=content_type,
        )

    def _get_sync(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].get_raw_stream().read()

    def _delete_sync(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)


def _build_client(config: CosArtifactConfig) -> CosS3Client:
    cos_config = CosConfig(
        Region=config.region.strip(),
        SecretId=config.secret_id.strip(),
        SecretKey=config.secret_key.strip(),
        Token=config.token.strip() or None,
        Scheme=config.scheme.strip().lower(),
        Endpoint=config.endpoint.strip() or None,
        Timeout=config.timeout_seconds,
        KeepAlive=True,
        PoolConnections=config.pool_max_size,
        PoolMaxSize=config.pool_max_size,
    )
    return CosS3Client(cos_config, retry=config.retry_times)


def _validate_config(config: CosArtifactConfig) -> None:
    """启动即校验;缺项直接失败,不带病运行。"""
    required = {
        "bucket": config.bucket,
        "region": config.region,
        "secret_id": config.secret_id,
        "secret_key": config.secret_key,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise StorageError(
            "artifact.cos_config_invalid",
            "COS 配置不完整:" + ",".join(missing),
            details={"missing": missing},
        )

    if config.scheme.strip().lower() not in {"http", "https"}:
        raise StorageError(
            "artifact.cos_config_invalid",
            f"非法的 COS scheme:{config.scheme!r}",
            details={"scheme": config.scheme},
        )
    endpoint = config.endpoint.strip()
    if endpoint and ("://" in endpoint or "/" in endpoint):
        raise StorageError(
            "artifact.cos_config_invalid",
            "COS endpoint 只接受 host,不要带协议或路径",
            details={"endpoint": config.endpoint},
        )
    if config.timeout_seconds <= 0:
        raise StorageError(
            "artifact.cos_config_invalid",
            "COS timeout_seconds 必须大于 0",
            details={"timeout_seconds": config.timeout_seconds},
        )
    if config.retry_times < 0:
        raise StorageError(
            "artifact.cos_config_invalid",
            "COS retry_times 不能为负数",
            details={"retry_times": config.retry_times},
        )
    if config.pool_max_size <= 0:
        raise StorageError(
            "artifact.cos_config_invalid",
            "COS pool_max_size 必须大于 0",
            details={"pool_max_size": config.pool_max_size},
        )


def _validate_key(key: str) -> None:
    """与 filesystem 后端同等级别的二次校验,防越界/空段。"""
    if not key or len(key) > _MAX_KEY_LENGTH or key.startswith("/"):
        raise StorageError("artifact.invalid_key", f"非法对象键:{key!r}")
    parts = key.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise StorageError("artifact.invalid_key", f"非法对象键:{key!r}")
    if any(not _SAFE_PART.match(part) for part in parts):
        raise StorageError("artifact.invalid_key", f"非法对象键:{key!r}")


def _clean_content_type(content_type: str | None) -> str:
    value = (content_type or "").strip()
    if (
        not value
        or len(value) > _MAX_CONTENT_TYPE_LENGTH
        or "\r" in value
        or "\n" in value
    ):
        return DEFAULT_CONTENT_TYPE
    return value


def _status_code(exc: CosServiceError) -> int | None:
    try:
        return int(exc.get_status_code())
    except (TypeError, ValueError):
        return None


def _error_code(exc: CosServiceError) -> str:
    try:
        return str(exc.get_error_code())
    except (KeyError, TypeError, AttributeError):
        return "Unknown"


def _request_id(exc: CosServiceError) -> str:
    try:
        return str(exc.get_request_id())
    except (KeyError, TypeError, AttributeError):
        return "Unknown"


def _service_error(
    action: str, bucket: str, key: str, exc: CosServiceError
) -> StorageError:
    return StorageError(
        f"artifact.cos_{action}_failed",
        f"COS 对象 {action} 失败",
        details={
            "bucket": bucket,
            "object_key": key,
            "status_code": _status_code(exc),
            "error_code": _error_code(exc),
            "request_id": _request_id(exc),
        },
    )


def _client_error(
    action: str, bucket: str, key: str, exc: CosClientError
) -> StorageError:
    return StorageError(
        f"artifact.cos_{action}_failed",
        f"COS 对象 {action} 失败",
        details={
            "bucket": bucket,
            "object_key": key,
            "error_type": type(exc).__name__,
        },
    )
