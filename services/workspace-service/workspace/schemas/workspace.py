"""工作区与交付物契约。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    status: str
    created_at: datetime


class ArtifactCreate(BaseModel):
    """交付物登记:正文随请求进来,服务端落对象存储。"""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=1_000_000)
    kind: str = Field(default="markdown", max_length=32)
    run_id: str | None = Field(default=None, max_length=32)
    workspace_id: str | None = Field(default=None, max_length=32)  # 不传走默认工作区


class ArtifactMeta(BaseModel):
    """元数据(列表用;不含正文)。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    run_id: str | None
    kind: str
    title: str
    storage: str
    size_bytes: int
    created_at: datetime


class ArtifactRead(ArtifactMeta):
    """元数据 + 正文(内容按存储后端装载)。"""

    content: str
