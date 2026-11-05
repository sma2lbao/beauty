"""认证契约(自建,由 dev 桩转正):注册/登录/刷新/内部签发。

密码策略:最小 8 位;哈希 argon2id(services/auth_service.py)。
账号即隔离边界:响应扁平暴露 account_id(= users.id),不再有组织/角色嵌套。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

PASSWORD_MIN = 8


class RegisterRequest(BaseModel):
    """注册:新建账号,返回可用令牌对。"""

    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    """登录:email + 密码换令牌对(email 全局唯一)。"""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """旋转式刷新:旧 refresh token 换新令牌对(旧令牌立即作废)。"""

    refresh_token: str = Field(min_length=20, max_length=128)


class LogoutRequest(BaseModel):
    """登出:吊销指定 refresh token(access 自然过期,不维护黑名单)。"""

    refresh_token: str = Field(min_length=20, max_length=128)


class TokenResponse(BaseModel):
    """注册/登录/刷新的统一响应:双令牌 + 账号身份(扁平)。"""

    model_config = ConfigDict(from_attributes=True)

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    account_id: str
    subject: str
    session_id: str


class IssueRequest(BaseModel):
    """内部服务签发:以目标账号为 claim 换 access token(服务间调用用)。"""

    account_id: str = Field(min_length=1, max_length=64, pattern=r"^[ -~]+$")
    subject: str = Field(default="svc-orchestrator", max_length=64)


class IssueResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    account_id: str
    subject: str
