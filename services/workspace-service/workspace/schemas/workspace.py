"""工作区与交付物契约。"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 文本正文按字符限;二进制按解码后的字节限(10 MiB)
CONTENT_MAX_CHARS = 1_000_000
BINARY_MAX_BYTES = 10 * 1024 * 1024


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class WorkspaceUpdate(BaseModel):
    """工作区部分更新:未传字段不改;description 显式 null 表示清空。

    区分"未传"与"显式 null"用 `model_fields_set`:name 只在非 None 时改,
    description 只要出现在请求体里就改(含 `null` 清空)。
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    status: str
    is_default: bool
    created_at: datetime


class ArtifactCreate(BaseModel):
    """交付物登记:正文随请求进来,服务端落对象存储。

    `content`(文本)与 `content_base64`(二进制字节)必须**恰好提供一个**;
    二进制交给 `content_base64`,避免把任意字节硬塞进 JSON 字符串。
    """

    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="markdown", max_length=32)
    run_id: str | None = Field(default=None, max_length=32)
    workspace_id: str | None = Field(default=None, max_length=32)  # 不传走默认工作区
    filename: str | None = Field(default=None, max_length=255)
    content_type: str | None = Field(default=None, max_length=128)
    content: str | None = Field(
        default=None, min_length=1, max_length=CONTENT_MAX_CHARS
    )
    content_base64: str | None = None

    @field_validator("content_base64")
    @classmethod
    def _valid_base64(cls, value: str | None) -> str | None:
        """base64 非法/解码为空/超上限都在契约层拒绝(422,不是 500)。"""
        if value is None:
            return None
        try:
            data = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 不是合法的 base64") from exc
        if not data:
            raise ValueError("content_base64 解码后为空")
        if len(data) > BINARY_MAX_BYTES:
            raise ValueError(f"二进制内容超过 {BINARY_MAX_BYTES} 字节上限")
        return value

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> Self:
        if (self.content is None) == (self.content_base64 is None):
            raise ValueError("content 与 content_base64 必须恰好提供一个")
        return self


class ArtifactMeta(BaseModel):
    """元数据(列表用;不含正文)。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    run_id: str | None
    kind: str
    title: str
    filename: str | None
    content_type: str | None
    storage: str
    size_bytes: int
    created_at: datetime


class ArtifactRead(ArtifactMeta):
    """元数据 + 正文(内容按存储后端装载);二进制交付物的 content 为 null。"""

    content: str | None
