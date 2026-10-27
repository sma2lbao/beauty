"""会话级工具调用审计 DTO:跨 run 聚合,含服务端生成的 unified diff。

tool-service 的 /v1/tool-calls 是审计域契约(字段与 ToolCallRead 一致),
orchestrator 原样透出,前端据此渲染补丁,不再从事件参数本地重建。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ConversationToolCallRead(BaseModel):
    """一条工具调用审计行;patch 仅在 include_patch=true 时有内容。"""

    id: str
    tool_call_id: str
    run_id: str | None = None
    conversation_id: str | None = None
    tool_name: str
    tool_version: str
    risk: str
    decision: str
    ok: bool
    error_code: str | None = None
    duration_ms: int
    result_chars: int
    bytes_written: int = 0
    truncated: bool
    sha256_before: str | None = None
    sha256_after: str | None = None
    paths: list[str] = Field(default_factory=list)
    # 写工具的服务端补丁:同一条记录里 diff 与行数统计配套使用。
    patch: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    patch_truncated: bool = False
    sandbox_id: str | None = None
    created_at: datetime


class ConversationToolCallPage(BaseModel):
    """一页审计行;degraded=true 表示 tool-service 不可达,前端回退本地重建。"""

    items: list[ConversationToolCallRead] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int
    degraded: bool = False
