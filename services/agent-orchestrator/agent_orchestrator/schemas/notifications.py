"""通知 API 的响应模型(通知持久化)。

读模型就是 notifications 表的一行:title/body 是落库时定好的展示文案与摘要,
tone 落库避免客户端再按状态映射;payload 保留原始 run 信息(状态、标题、目标)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

NotificationTone = Literal["info", "success", "warning", "danger"]


class NotificationRead(BaseModel):
    """一条站内通知(未读时 read_at 为空)。"""

    id: str
    kind: str
    title: str
    body: str
    tone: NotificationTone
    to_path: str | None = None
    run_id: str | None = None
    conversation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    read_at: datetime | None = None


class NotificationList(BaseModel):
    """未读清单:items 按时间倒序,next_cursor 不为空时用它翻下一页。"""

    items: list[NotificationRead]
    unread_count: int
    next_cursor: str | None = None


class NotificationReadAll(BaseModel):
    """全部已读的结果:updated 是本次改动的行数。"""

    updated: int
