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
MemoryScope = Literal["account", "workspace"]
MemoryInjectionMode = Literal["retrieved", "always"]


class MemoryCreate(BaseModel):
    """写入记忆的请求体;ttl_days 不传=部署默认,0=永久,>0=N 天。"""

    content: str = Field(min_length=1, max_length=8000)
    kind: MemoryKind = "note"
    scope: MemoryScope = "account"
    workspace_id: str | None = Field(default=None, max_length=32)
    pinned: bool = False
    ttl_days: int | None = Field(default=None, ge=0, le=3650)
    injection_mode: MemoryInjectionMode = "retrieved"


class MemoryUpdate(BaseModel):
    """局部更新记忆(PATCH);未提供的字段保持不变。

    expected_updated_at 用于乐观并发:带上客户端读到的 updated_at,服务端发现
    已被他人修改时返回 409,避免两端互相覆盖。ttl_days 只在显式出现时生效
    (None 会清空过期时间?不——None 表示"维持现状",0 表示永久)。
    """

    content: str | None = Field(default=None, min_length=1, max_length=8000)
    kind: MemoryKind | None = None
    scope: MemoryScope | None = None
    workspace_id: str | None = Field(default=None, max_length=32)
    pinned: bool | None = None
    ttl_days: int | None = Field(default=None, ge=0, le=3650)
    injection_mode: MemoryInjectionMode | None = None
    expected_updated_at: datetime | None = None


class MemorySearchRequest(BaseModel):
    """检索请求体(POST /memories/search):命中会累加访问统计。"""

    query: str = Field(min_length=1, max_length=2000)
    workspace_id: str | None = Field(default=None, max_length=32)
    kind: MemoryKind | None = None
    limit: int = Field(default=5, ge=1, le=20)


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
    injection_mode: str = "retrieved"
    confidence: float = 1.0
    valid_from: datetime | None = None
    invalid_at: datetime | None = None
    superseded_by: str | None = None
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
    """账号记忆计数(pending_reviews 是复核队列里的待处理条数)。"""

    total: int
    active: int
    pinned: int
    expired: int
    superseded: int = 0
    pending_reviews: int = 0


class MemoryPurgeResult(BaseModel):
    """过期清理结果(软删标记条数)。"""

    purged: int


class MemoryPurgeAllResult(BaseModel):
    """账号记忆彻底删除结果(物理删除,不可恢复)。"""

    memories: int
    reviews: int


class MemoryExport(BaseModel):
    """账号记忆导出(可携带全部正文与元数据,用于迁移/备份)。"""

    exported_at: datetime
    memories: list[MemoryRead]


class MemoryReviewRead(BaseModel):
    """待复核候选(低置信度自动抽取);不返回 embedding。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str | None = None
    scope: str
    kind: str
    content: str
    confidence: float
    evidence_quote: str | None = None
    source_type: str
    source_id: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class MemoryReviewDecision(BaseModel):
    """复核决定:可选备注;通过后按候选内容写入长期记忆。"""

    note: str | None = Field(default=None, max_length=2000)


class MemoryReembedRequest(BaseModel):
    """重建向量请求:dry_run=True 只统计不写库,便于先看影响面。

    limit 是本次最多处理的条数(默认取部署配置),分多次调用可渐进完成。
    """

    dry_run: bool = False
    limit: int | None = Field(default=None, ge=1, le=2000)


class MemoryReembedResult(BaseModel):
    """重建向量结果:scanned 为扫描条数,updated 为实际重建条数。"""

    scanned: int
    updated: int
    skipped: int = 0
    model: str
    dry_run: bool = False


class MemoryImportItem(BaseModel):
    """可导入的一条记忆:字段与导出格式对齐,额外字段(如 id)被忽略。"""

    content: str = Field(min_length=1, max_length=8000)
    kind: MemoryKind = "note"
    scope: MemoryScope = "account"
    workspace_id: str | None = Field(default=None, max_length=32)
    pinned: bool = False
    ttl_days: int = Field(default=0, ge=0, le=3650)
    injection_mode: MemoryInjectionMode = "retrieved"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_type: Literal["manual", "chat", "run", "tool"] = "manual"
    source_id: str | None = Field(default=None, max_length=64)


class MemoryImportRequest(BaseModel):
    """批量导入请求:直接吃 GET /memories/export 的 memories 数组。"""

    dry_run: bool = False
    memories: list[MemoryImportItem] = Field(min_length=1, max_length=500)


class MemoryImportError(BaseModel):
    """被跳过的条目:按数组下标定位,code 与写入接口的错误码同口径。"""

    index: int
    code: str
    message: str


class MemoryImportResult(BaseModel):
    """导入结果:近重复与非法条目分别计数,便于前端展示跳过原因。"""

    received: int
    created: int
    skipped_duplicate: int
    skipped_invalid: int
    dry_run: bool = False
    errors: list[MemoryImportError] = Field(default_factory=list)
