"""通用二进制对象(P3):skill 包按租户前缀落 ArtifactStore,不登记 DB 行。

对象键永远是 ``<tenant_id>/objects/<uuid4hex><安全后缀>``;
读取/删除时强校验租户前缀——越权键与不存在统一 404,不泄漏其他租户
对象是否存在。存储后端为 database(store=None)时明确 503,不静默降级。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import uuid4

from common.artifacts import ArtifactStore
from common.errors import AppError, NotFoundError, StorageError

from workspace.core.config import Settings
from workspace.schemas.object import ObjectCreate

OBJECTS_PART = "objects"
MAX_SUFFIX_LENGTH = 16
# 租户标识直接来自 token claim,拼键前仍按安全字符校验(防路径穿越)
_SAFE_PART = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_SUFFIX = re.compile(rf"^\.[A-Za-z0-9]{{1,{MAX_SUFFIX_LENGTH}}}$")


class ObjectStoreUnavailableError(StorageError):
    """未配置对象存储后端(ARTIFACT_STORE=database):二进制对象明确不可用。"""

    status = 503


@dataclass(frozen=True, slots=True)
class StoredObject:
    """写入结果:对象键、字节数与内容摘要。"""

    key: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class LoadedObject:
    """读取结果:对象键、原始字节与内容摘要。"""

    key: str
    data: bytes
    sha256: str

    @property
    def size_bytes(self) -> int:
        return len(self.data)


def _require_store(store: ArtifactStore | None) -> ArtifactStore:
    if store is None:
        raise ObjectStoreUnavailableError(
            "object.store_missing",
            "当前未配置对象存储后端(ARTIFACT_STORE=database),不能存取二进制对象",
        )
    return store


def _require_tenant(tenant_id: str) -> str:
    if not tenant_id or not _SAFE_PART.match(tenant_id):
        raise AppError("object.invalid_tenant", "租户标识非法,无法定位对象")
    return tenant_id


def build_object_key(tenant_id: str, filename: str | None) -> str:
    """生成对象键:租户前缀 + objects/ + uuid4(hex) + 安全后缀。"""
    return (
        f"{_require_tenant(tenant_id)}/{OBJECTS_PART}/"
        f"{uuid4().hex}{_safe_suffix(filename)}"
    )


def _safe_suffix(filename: str | None) -> str:
    """只取 basename 的安全后缀(小写 .zip/.skill 等),其余一律丢弃。"""
    if not filename:
        return ""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = PurePosixPath(base).suffix
    return suffix.lower() if _SAFE_SUFFIX.match(suffix) else ""


def _require_owned_key(tenant_id: str, key: str) -> str:
    """强校验 key 属于当前租户;不满足一律 404(与不存在不可区分)。"""
    prefix = f"{_require_tenant(tenant_id)}/{OBJECTS_PART}/"
    name = key[len(prefix) :] if key.startswith(prefix) else ""
    if not name or "/" in name or name in {".", ".."} or not _SAFE_PART.match(name):
        raise NotFoundError("object.not_found", "对象不存在", details={"key": key})
    return key


def _max_base64_length(max_bytes: int) -> int:
    return 4 * ((max_bytes + 2) // 3)


def _too_large(max_bytes: int) -> AppError:
    return AppError(
        "object.too_large",
        f"对象超过大小上限:{max_bytes} 字节",
        details={"max_bytes": max_bytes},
    )


def _decode(content_base64: str, max_bytes: int) -> bytes:
    """解码 Base64;先按长度上限快速拒绝,避免为超大请求分配解码缓冲。"""
    if len(content_base64) > _max_base64_length(max_bytes):
        raise _too_large(max_bytes)
    try:
        payload = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError(
            "object.content_invalid", "content_base64 不是合法的 Base64"
        ) from exc
    if len(payload) > max_bytes:
        raise _too_large(max_bytes)
    return payload


async def put_object(
    tenant_id: str,
    data: ObjectCreate,
    store: ArtifactStore | None,
    settings: Settings,
) -> StoredObject:
    """写入对象:Base64 解码 → 大小校验 → 租户前缀键落存储。"""
    store = _require_store(store)
    payload = _decode(data.content_base64, settings.object_max_bytes)
    key = build_object_key(tenant_id, data.filename)
    await store.put(key, payload, content_type=data.content_type)
    return StoredObject(
        key=key,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


async def get_object(
    tenant_id: str, key: str, store: ArtifactStore | None
) -> LoadedObject:
    """读取对象:key 必须属于当前租户,否则 404;不存在同样 404。"""
    store = _require_store(store)
    key = _require_owned_key(tenant_id, key)
    payload = await store.get(key)
    if payload is None:
        raise NotFoundError("object.not_found", "对象不存在", details={"key": key})
    return LoadedObject(
        key=key, data=payload, sha256=hashlib.sha256(payload).hexdigest()
    )


async def delete_object(tenant_id: str, key: str, store: ArtifactStore | None) -> None:
    """删除对象(幂等):只删对象,不动任何 DB 行;越权键 404。"""
    store = _require_store(store)
    key = _require_owned_key(tenant_id, key)
    await store.delete(key)
