"""OAuth 2.1 授权服务器元数据(RFC 8414 / MCP discovery)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from account.api.deps import get_settings
from account.core.config import Settings
from account.schemas.oauth21 import DEFAULT_OAUTH21_SCOPES

router = APIRouter()
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(settings: SettingsDep) -> dict:
    issuer = settings.auth_issuer.rstrip("/")
    web = settings.oauth21_web_origin.rstrip("/")
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{web}/oauth/authorize",
        "token_endpoint": f"{issuer}/v1/oauth/token",
        "registration_endpoint": f"{issuer}/v1/oauth/register",
        "scopes_supported": list(DEFAULT_OAUTH21_SCOPES),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }
