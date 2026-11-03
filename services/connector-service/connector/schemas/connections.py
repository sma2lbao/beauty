"""连接 DTO:凭证只进不出(所有读端都不含凭证字段)。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"  # 凭证失效,需要重新授权


class ConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    account: str
    display_name: str | None
    status: ConnectionStatus
    enabled: bool
    auth_kind: str | None = None
    provider_account_id: str | None = None
    external_workspace: str | None = None
    scopes: list[str] | None = None
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_error_code: str | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
