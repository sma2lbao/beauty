"""MCP OAuth 2.1 契约(M3-04)。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

OAUTH21_SCOPES = frozenset({"mcp:tools:read", "mcp:tools:call", "artifacts:read"})
DEFAULT_OAUTH21_SCOPES = ["mcp:tools:read", "mcp:tools:call", "artifacts:read"]


class OAuthClientRegister(BaseModel):
    client_name: str = Field(min_length=1, max_length=120)
    redirect_uris: list[str] = Field(min_length=1, max_length=10)
    scope: str | None = Field(default=None, max_length=500)


class OAuthClientRead(BaseModel):
    client_id: str
    client_name: str
    redirect_uris: list[str]
    scopes: list[str]
    token_endpoint_auth_method: Literal["none"] = "none"
    grant_types: list[str] = ["authorization_code", "refresh_token"]
    response_types: list[str] = ["code"]


class AuthorizeApprove(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    redirect_uri: str = Field(min_length=1, max_length=500)
    scope: str = Field(default="", max_length=500)
    state: str | None = Field(default=None, max_length=500)
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: Literal["S256"] = "S256"


class AuthorizeResult(BaseModel):
    redirect_to: str


class AuthorizedClientRead(BaseModel):
    client_id: str
    client_name: str
    scopes: list[str]
    authorized_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str
    scope: str


class OAuthIntrospectRead(BaseModel):
    active: bool
    tenant_id: str | None = None
    client_id: str | None = None
    scope: str | None = None
    exp: int | None = None
