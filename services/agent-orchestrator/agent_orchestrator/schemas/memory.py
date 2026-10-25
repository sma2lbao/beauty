"""记忆 API 的请求/响应模型(记忆能力一期)。

读模型不返回 embedding(存储实现细节);枚举用 Literal 让 OpenAPI 自带
可选值,前端不用猜。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 记忆类型与作用域:与 services.memory 的常量保持同一份字面量。
MemoryKind = Literal["note", "preference", "fact", "procedure"]
MemoryScope = Literal["tenant", "workspace"]


class MemoryCreate(BaseModel):
    """写入记忆的请求体;ttl_days=0/None 表示不过期。"""

    content: str = Field(min_length=1, max_length=8000)
    kind: MemoryKind = "note"
    scope: MemoryScope = "tenant"
    workspace_id: str | None = Field(default=None, max_length=32)
    pinned: bool = False
    ttl_days: int | None = Field(default=None, ge=0, le=3650)


class MemoryRead(BaseModel):
    """记忆读模型(管理面/检索结果共用)。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str | None = None
    scope: str
    kind: str
    content: str
    pinned: bool
    source_type: str
    source_id: str | None = None
    expires_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int
    created_at: datetime
    updated_at: datetime


class MemorySearchResponse(BaseModel):
    """检索结果:回显 query 方便调用方对齐(流式/并发场景)。"""

    query: str
    hits: list[MemoryRead]


class MemoryStatsRead(BaseModel):
    """租户记忆计数。"""

    total: int
    active: int
    pinned: int
    expired: int


class MemoryPurgeResult(BaseModel):
    """过期清理结果(软删标记条数)。"""

    purged: int
