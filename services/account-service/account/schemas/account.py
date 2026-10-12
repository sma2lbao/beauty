"""账号域契约:组织档案、成员、会话。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    plan: str
    quota: dict[str, Any]
    created_at: datetime


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    plan: str | None = Field(default=None, min_length=1, max_length=32)
    quota: dict[str, Any] | None = None


class UserCreate(BaseModel):
    """成员登记:subject 必须与 IdP 签发的一致。"""

    subject: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=120)
    role: Literal["owner", "member"] = "member"


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    role: Literal["owner", "member"] | None = None
    status: Literal["active", "disabled"] | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subject: str
    email: str
    display_name: str | None
    role: str
    status: str
    created_at: datetime


class SessionCreate(BaseModel):
    user_id: str = Field(min_length=1)
    device: str = Field(min_length=1, max_length=120)


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    device: str
    status: str
    last_seen_at: datetime | None
    created_at: datetime
