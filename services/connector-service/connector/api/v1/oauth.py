"""Gmail / Microsoft 365 OAuth 连接端点(M3 阶段 3A)。"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from common.errors import AppError
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from connector.api.deps import get_session, get_settings, get_tenant_id
from connector.core.config import Settings
from connector.services import oauth as oauth_service

router = APIRouter(prefix="/oauth", tags=["oauth"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/providers")
async def providers(settings: SettingsDep) -> list[dict]:
    return [
        {
            "provider": "google",
            "label": "Google / Gmail",
            "enabled": bool(
                settings.google_client_id and settings.google_client_secret
            ),
        },
        {
            "provider": "microsoft",
            "label": "Microsoft 365 / Outlook",
            "enabled": bool(
                settings.microsoft_client_id and settings.microsoft_client_secret
            ),
        },
    ]


@router.get("/{provider}/start")
async def start(
    provider: str, tenant_id: TenantId, settings: SettingsDep
) -> dict[str, str]:
    return {
        "authorize_url": oauth_service.authorize_url(provider, tenant_id, settings)
    }


@router.get("/{provider}/callback")
async def callback(
    provider: str,
    request: Request,
    session: DBSession,
    settings: SettingsDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    target = settings.oauth_web_base.rstrip("/") + "/connectors"
    if error or not code or not state:
        query = urlencode({"oauth": "error", "oauth_error": error or "missing_code"})
        return RedirectResponse(f"{target}?{query}")
    try:
        connection, created = await oauth_service.complete_oauth(
            session,
            provider,
            code,
            state,
            settings,
            transport=getattr(request.app.state, "oauth_transport", None),
        )
    except AppError as exc:
        query = urlencode({"oauth": "error", "oauth_error": exc.message})
        return RedirectResponse(f"{target}?{query}")
    query = urlencode(
        {
            "oauth": "success",
            "provider": connection.provider,
            "account": connection.account,
            "created": "1" if created else "0",
        }
    )
    return RedirectResponse(f"{target}?{query}")
