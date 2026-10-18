"""MCP Client 契约(M1 本地版)。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MCPToolRead(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    url: str = Field(min_length=8, max_length=500, pattern=r"^https?://")
    token: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    tools_allowlist: list[str] = Field(default_factory=list, max_length=100)
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class MCPServerUpdate(BaseModel):
    name: str | None = Field(
        default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$"
    )
    url: str | None = Field(default=None, min_length=8, max_length=500, pattern=r"^https?://")
    token: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None
    tools_allowlist: list[str] | None = Field(default=None, max_length=100)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)


class MCPServerRead(BaseModel):
    id: str
    name: str
    url: str
    enabled: bool
    has_token: bool
    token_hint: str | None
    tools_allowlist: list[str]
    tools: list[MCPToolRead]
    timeout_seconds: int
    last_status: str | None
    last_checked_at: datetime | None
    last_error: str | None
    created_at: datetime


class MCPTestRead(BaseModel):
    ok: bool
    detail: str
    tools: list[MCPToolRead]
