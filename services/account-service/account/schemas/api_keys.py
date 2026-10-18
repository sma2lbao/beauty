"""API Key 契约(M3-01)。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

ALLOWED_SCOPES = frozenset(
    {"mcp:tools:read", "mcp:tools:call", "artifacts:read"}
)
DEFAULT_SCOPES = ["mcp:tools:read", "mcp:tools:call", "artifacts:read"]


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    prefix: str
    key_hint: str
    masked_key: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreated(ApiKeyRead):
    key: str


class ApiKeyVerifyRequest(BaseModel):
    key: str = Field(min_length=8, max_length=200)


class ApiKeyVerifyRead(BaseModel):
    tenant_id: str
    api_key_id: str
    scopes: list[str]
