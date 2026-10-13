"""dev 登录契约(桩):请求声明身份,响应携带 access token。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DevLoginRequest(BaseModel):
    """声明 email 即登录(无密码,见 core/dev_idp.py 的定位说明)。"""

    email: str = Field(min_length=3, max_length=320)
    # 不指定组织就新开一个(组织 id 即租户标识,§7)
    organization_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[ -~]+$"
    )
    organization_name: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)


class DevLoginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    tenant_id: str
    organization_id: str
    user_id: str
    subject: str
    role: str
    session_id: str


class DevIssueRequest(BaseModel):
    """内部服务签发:以目标租户为 claim 换 access token(服务间调用用)。"""

    tenant_id: str = Field(min_length=1, max_length=64, pattern=r"^[ -~]+$")
    subject: str = Field(default="svc-orchestrator", max_length=64)


class DevIssueResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    tenant_id: str
    subject: str
