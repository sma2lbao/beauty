"""OAuth 授权会话:state 只落随机 nonce,身份靠服务端行查回(RFC 9700)。

- 授权页 URL 由 provider 拼(含 PKCE S256);
- 回调无用户身份,只能按 nonce 取会话;一次性消费,重复一律失败;
- token 落库成功后才置 completed,回前端只暴露白名单错误码。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from connector.core.clock import as_utc, utcnow
from connector.core.config import Settings
from connector.core.http import open_client
from connector.providers import build_provider, require_provider
from connector.providers.errors import ProviderError
from connector.repositories import OAuthSessionRepository
from connector.services import connections as connections_service

_STATUS_PENDING = "pending"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"
_STATUS_EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class AuthorizeStart:
    session_id: str
    authorize_url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CallbackResult:
    provider: str
    status: str  # connected | error
    code: str | None = None


def build_redirect_uri(settings: Settings, provider_name: str) -> str:
    base = settings.oauth_redirect_base.rstrip("/")
    return f"{base}/v1/oauth/{provider_name}/callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def start_authorize(
    session: AsyncSession,
    settings: Settings,
    *,
    tenant_id: str,
    provider_name: str,
    owner_subject: str | None,
) -> AuthorizeStart:
    provider = require_provider(settings, provider_name)
    if not provider.configured:
        raise ConflictError(
            "connector.provider_not_configured",
            f"{provider.display_name} 尚未配置应用凭证",
            details={"provider": provider.name},
        )
    verifier, challenge = _pkce()
    state_nonce = secrets.token_urlsafe(32)
    redirect_uri = build_redirect_uri(settings, provider.name)
    expires_at = utcnow() + timedelta(seconds=settings.oauth_session_ttl_seconds)
    repo = OAuthSessionRepository(session)
    row = await repo.add(
        repo.model(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            provider=provider.name,
            state_nonce=state_nonce,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            scopes=list(provider.scopes()),
            status=_STATUS_PENDING,
            expires_at=expires_at,
        )
    )
    url = provider.authorize_url(
        state=state_nonce, code_challenge=challenge, redirect_uri=redirect_uri
    )
    return AuthorizeStart(session_id=row.id, authorize_url=url, expires_at=expires_at)


async def handle_callback(
    session: AsyncSession,
    settings: Settings,
    provider_name: str,
    *,
    code: str | None,
    state: str | None,
    error: str | None,
    transport: httpx.AsyncBaseTransport | None,
) -> CallbackResult:
    provider = build_provider(settings, provider_name)
    if provider is None:
        return CallbackResult(provider_name, "error", "connector.provider_not_found")
    repo = OAuthSessionRepository(session)
    row = await repo.get_by_state(state or "")
    if row is None or row.provider != provider.name:
        return CallbackResult(provider.name, "error", "connector.invalid_state")
    if row.status != _STATUS_PENDING:
        return CallbackResult(provider.name, "error", "connector.state_used")
    if as_utc(row.expires_at) <= utcnow():
        await _finish(session, row, _STATUS_EXPIRED, "connector.state_expired")
        return CallbackResult(provider.name, "error", "connector.state_expired")
    if error or not code:
        await _finish(session, row, _STATUS_FAILED, "connector.oauth_denied")
        return CallbackResult(provider.name, "error", "connector.oauth_denied")

    async with open_client(settings, transport) as client:
        try:
            credentials = await provider.exchange_code(
                code=code,
                code_verifier=row.code_verifier or "",
                redirect_uri=row.redirect_uri,
                client=client,
            )
            identity = await provider.fetch_identity(credentials, client)
        except (ProviderError, ValueError) as exc:
            code_value = (
                exc.code if isinstance(exc, ProviderError) else "connector.oauth_failed"
            )
            await _finish(session, row, _STATUS_FAILED, code_value)
            return CallbackResult(provider.name, "error", code_value)

    conn = await connections_service.upsert_authorized_connection(
        session,
        settings=settings,
        tenant_id=row.tenant_id,
        provider_name=provider.name,
        owner_subject=row.owner_subject,
        credentials=credentials,
        identity=identity,
        scopes=list(credentials.scope.split()) if credentials.scope else [],
    )
    await _finish(session, row, _STATUS_COMPLETED, None, connection_id=conn.id)
    return CallbackResult(provider.name, "connected", conn.id)


async def _finish(
    session: AsyncSession,
    row: Any,
    status: str,
    error_code: str | None,
    *,
    connection_id: str | None = None,
) -> None:
    row.status = status
    row.error_code = error_code
    row.completed_at = utcnow()
    if connection_id is not None:
        row.connection_id = connection_id
    await session.flush()


def success_redirect(settings: Settings, result: CallbackResult) -> str:
    """回前端的 302 地址(前端据此刷新/提示,不接收任何 token)。"""
    base = settings.oauth_success_redirect
    params = {"provider": result.provider, "status": result.status}
    if result.status == "connected":
        params["connection_id"] = result.code or ""
    else:
        params["code"] = result.code or "connector.oauth_failed"
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode(params)}"
