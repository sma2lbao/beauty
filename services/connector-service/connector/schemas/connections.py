"""连接 DTO:凭证只进不出(所有读端都不含凭证字段)。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"  # 凭证失效(保留枚举值:历史数据可能仍是该状态)


class ConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    account: str
    display_name: str | None
    status: ConnectionStatus
    last_checked_at: datetime | None
    created_at: datetime
