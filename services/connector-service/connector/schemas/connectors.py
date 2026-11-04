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


class ConnectionUpdate(BaseModel):
    """PATCH 只允许改展示名与启用开关;凭证/scope 只能通过重新授权变更。"""

    display_name: str | None = None
    enabled: bool | None = None
