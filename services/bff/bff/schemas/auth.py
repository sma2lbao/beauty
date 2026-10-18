"""BFF 会话契约:登录只返回用户信息,双令牌永远不下发给 SPA。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

PASSWORD_MIN = 8


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)


class SessionUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    tenant_id: str
    organization_id: str
    subject: str
    email: str
    display_name: str | None = None
    role: str
    session_id: str


class SessionRead(BaseModel):
    authenticated: bool = True
    user: SessionUser
    expires_at: datetime


class StreamTokenRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class StreamTokenResponse(BaseModel):
    token: str
    token_type: str = "Bearer"
    expires_in: int
    url: str
