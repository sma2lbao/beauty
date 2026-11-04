"""连接器目录/授权/执行 DTO。

凭证永远不出现在这些模型里;连接详情用 ConnectionRead(schemas/connections.py)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ToolInfo(BaseModel):
    """一个 provider 能力在目录里的展示形态。"""

    name: str = Field(description="完整工具名,如 Acme__read_document")
    title: str
    description: str
    risk: str = "read"


class ConnectionBrief(BaseModel):
    """目录里附带的最小连接状态(详情走 /v1/connections/{id})。"""

    id: str
    status: str
    account: str
    display_name: str | None = None
    enabled: bool
    expires_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_error_code: str | None = None


class ProviderInfo(BaseModel):
    name: str
    display_name: str
    description: str
    auth_kind: str
    scopes: list[str]
    docs_url: str
    configured: bool = Field(description="应用凭证是否齐备;false 时不能发起授权")
    token_connectable: bool = Field(
        default=False,
        description="支持用户粘贴静态令牌直接建连接(不需要应用凭证)",
    )
    connected: bool = Field(description="存在 active 且 enabled 的连接")
    connection: ConnectionBrief | None = None
    tools: list[ToolInfo]


class ConnectorsRead(BaseModel):
    providers: list[ProviderInfo]


class AuthorizeResponse(BaseModel):
    """发起授权:前端 302/跳转到 authorize_url。"""

    provider: str
    authorize_url: str
    expires_at: datetime


class ExecuteRequest(BaseModel):
    """tool-service -> connector-service 的执行请求(参数已过 JSON Schema 校验)。"""

    tool: str = Field(description="完整工具名,如 Acme__read_document")
    arguments: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None


class ConnectionTokenCreate(BaseModel):
    """粘贴静态令牌建连接(如内部集成令牌):无 OAuth、无回调。"""

    provider: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9_-]{0,31}$",
        description="provider key,如 notion",
    )
    token: str = Field(
        min_length=8,
        max_length=4096,
        description="第三方集成令牌;只随本次请求传输,服务端加密落库",
    )
    display_name: str | None = Field(
        default=None, max_length=120, description="自定义展示名,不填用 provider 身份"
    )


class ConnectionUpdate(BaseModel):
    """PATCH 只允许改展示名与启用开关;凭证/scope 只能通过重新授权变更。"""

    display_name: str | None = None
    enabled: bool | None = None
