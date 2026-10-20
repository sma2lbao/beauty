"""通用二进制对象端点(P3):skill 包上传/下载/删除,不登记 DB 行。"""

from __future__ import annotations

import base64
from typing import Annotated

from common.artifacts import ArtifactStore
from fastapi import APIRouter, Depends, Query, Response, status

from workspace.api.deps import get_artifact_store, get_settings, get_tenant_id
from workspace.core.config import Settings
from workspace.schemas.object import (
    MAX_KEY_LENGTH,
    ObjectCreate,
    ObjectMeta,
    ObjectRead,
)
from workspace.services import objects as objects_service

router = APIRouter(prefix="/objects", tags=["objects"])

TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[ArtifactStore | None, Depends(get_artifact_store)]
ObjectKey = Annotated[str, Query(min_length=1, max_length=MAX_KEY_LENGTH)]


@router.post("", response_model=ObjectMeta, status_code=status.HTTP_201_CREATED)
async def create_object(
    body: ObjectCreate,
    tenant_id: TenantId,
    settings: SettingsDep,
    store: StoreDep,
) -> ObjectMeta:
    """上传对象:返回对象键/字节数/sha256(内容用 Base64 提交)。"""
    stored = await objects_service.put_object(tenant_id, body, store, settings)
    return ObjectMeta(
        key=stored.key, size_bytes=stored.size_bytes, sha256=stored.sha256
    )


@router.get("", response_model=ObjectRead)
async def read_object(
    key: ObjectKey, tenant_id: TenantId, store: StoreDep
) -> ObjectRead:
    """读取对象:跨租户/不存在统一 404。"""
    loaded = await objects_service.get_object(tenant_id, key, store)
    return ObjectRead(
        key=loaded.key,
        content_base64=base64.b64encode(loaded.data).decode("ascii"),
        size_bytes=loaded.size_bytes,
        sha256=loaded.sha256,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_object(
    key: ObjectKey, tenant_id: TenantId, store: StoreDep
) -> Response:
    """删除对象(幂等):只删对象,不删任何 DB 行。"""
    await objects_service.delete_object(tenant_id, key, store)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
