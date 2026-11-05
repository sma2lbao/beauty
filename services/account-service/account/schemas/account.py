"""账号域契约:账号档案、会话。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str | None
    status: str
    created_at: datetime


class AccountUpdate(BaseModel):
    """PATCH /v1/me:只允许改显示名(其余字段由服务端维护)。"""

    display_name: str | None = Field(default=None, max_length=120)


class SessionCreate(BaseModel):
    """登记设备会话:账号由 access token 决定,只需描述设备。"""

    device: str = Field(min_length=1, max_length=120)


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device: str
    status: str
    last_seen_at: datetime | None
    created_at: datetime
