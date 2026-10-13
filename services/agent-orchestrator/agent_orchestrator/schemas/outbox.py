"""outbox 运维面契约:可见(列表/计数)+ 可人工重放(§6.3)。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class OutboxEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    status: str
    attempts: int
    payload: dict[str, Any]
    available_at: datetime
    dispatched_at: datetime | None
    last_error: str | None
    created_at: datetime


class OutboxStatsRead(BaseModel):
    """各状态计数:控制台徽标与告警用。"""

    pending: int
    dispatched: int
    failed: int


class RequeueResultRead(BaseModel):
    """人工重放结果:重放了几条。"""

    requeued: int
