"""MCP OAuth 2.1 授权服务器业务(M3-04)。

授权码 + PKCE(S256)+ 公开客户端;access/refresh token 均为不透明串,
DB 只存 SHA-256 哈希,MCP Server 通过内部 introspect 换租户/scopes。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from common.errors import AppError
from sqlalchemy.ext.asyncio import AsyncSession

from account.core.config import Settings
from account.models.account import OAuthAuthorizationCode, OAuthClient, OAuthToken
from account.repositories import (
    OAuthClientRepository,
    OAuthCodeRepository,
    OAuthTokenRepository,
)
from account.schemas.oauth21 import (
    DEFAULT_OAUTH21_SCOPES,
    OAUTH21_SCOPES,
    AuthorizeApprove,
    AuthorizeResult,
    OAuthClientRead,
    OAuthClientRegister,
    OAuthIntrospectRead,
    TokenResponse,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_redirect_uri(uri: str) -> None:
    parsed = urlsplit(uri)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError("oauth.invalid_redirect_uri", f"redirect_uri 非法:{uri}")
    if parsed.fragment:
        raise AppError("oauth.invalid_redirect_uri", "redirect_uri 不能带 fragment")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"):
        raise AppError(
            "oauth.invalid_redirect_uri",
            "本地 http redirect_uri 只允许 localhost/127.0.0.1",
        )


def _parse_scopes(raw: str | None, allowed: list[str]) -> list[str]:
    requested = (raw or "").split()
    if not requested:
        return list(allowed)
    invalid = [scope for scope in requested if scope not in OAUTH21_SCOPES]
    if invalid:
        raise AppError("oauth.invalid_scope", f"不支持的 scope:{', '.join(invalid)}")
    if any(scope not in allowed for scope in requested):
        raise AppError("oauth.invalid_scope", "请求的 scope 超出客户端注册范围")
    return requested


async def register_client(
    session: AsyncSession, data: OAuthClientRegister
) -> OAuthClientRead:
    if not data.redirect_uris:
        raise AppError("oauth.invalid_redirect_uri", "至少需要一个 redirect_uri")
    for uri in data.redirect_uris:
        _validate_redirect_uri(uri)
    scopes = _parse_scopes(data.scope, list(DEFAULT_OAUTH21_SCOPES))
    client_id = "bm_client_" + secrets.token_urlsafe(18)
    client = OAuthClient(
        client_id=client_id,
        client_name=data.client_name,
        redirect_uris=list(dict.fromkeys(data.redirect_uris)),
        scopes=scopes,
    )
    await OAuthClientRepository(session).add(client)
    return OAuthClientRead(
        client_id=client.client_id,
        client_name=client.client_name,
        redirect_uris=list(client.redirect_uris),
        scopes=list(client.scopes),
    )


async def get_client(session: AsyncSession, client_id: str) -> OAuthClientRead:
    client = await OAuthClientRepository(session).find(client_id)
    if client is None:
        raise AppError("oauth.client_not_found", "OAuth 客户端不存在", details={})
    return OAuthClientRead(
        client_id=client.client_id,
        client_name=client.client_name,
        redirect_uris=list(client.redirect_uris),
        scopes=list(client.scopes),
    )


async def authorize(
    session: AsyncSession,
    tenant_id: str,
    data: AuthorizeApprove,
    settings: Settings,
) -> AuthorizeResult:
    client = await OAuthClientRepository(session).find(data.client_id)
    if client is None:
        raise AppError("oauth.client_not_found", "OAuth 客户端不存在")
    if data.redirect_uri not in (client.redirect_uris or []):
        raise AppError("oauth.invalid_redirect_uri", "redirect_uri 未注册")
    if data.code_challenge_method != "S256":
        raise AppError("oauth.invalid_request", "只支持 PKCE S256")
    scopes = _parse_scopes(data.scope, list(client.scopes or []))
    code = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=settings.oauth21_code_ttl_seconds)
    await OAuthCodeRepository(session).add(
        OAuthAuthorizationCode(
            tenant_id=tenant_id,
            code_hash=_hash(code),
            client_id=client.client_id,
            user_id=None,
            redirect_uri=data.redirect_uri,
            scopes=scopes,
            code_challenge=data.code_challenge,
            expires_at=expires_at,
        )
    )
    parts = urlsplit(data.redirect_uri)
    query = list(parse_qsl(parts.query, keep_blank_values=True))
    query.append(("code", code))
    if data.state is not None:
        query.append(("state", data.state))
    return AuthorizeResult(
        redirect_to=urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
    )


def _verify_pkce(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(expected, challenge)


async def _issue_tokens(
    session: AsyncSession,
    tenant_id: str,
    client_id: str,
    user_id: str | None,
    scopes: list[str],
    settings: Settings,
) -> TokenResponse:
    access = "bm_oauth_" + secrets.token_urlsafe(32)
    refresh = "bm_refresh_" + secrets.token_urlsafe(32)
    now = _now()
    await OAuthTokenRepository(session).add(
        OAuthToken(
            tenant_id=tenant_id,
            access_hash=_hash(access),
            refresh_hash=_hash(refresh),
            client_id=client_id,
            user_id=user_id,
            scopes=scopes,
            access_expires_at=now
            + timedelta(seconds=settings.oauth21_access_ttl_seconds),
            refresh_expires_at=now
            + timedelta(seconds=settings.oauth21_refresh_ttl_seconds),
        )
    )
    return TokenResponse(
        access_token=access,
        expires_in=settings.oauth21_access_ttl_seconds,
        refresh_token=refresh,
        scope=" ".join(scopes),
    )


async def exchange_code(
    session: AsyncSession,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
    settings: Settings,
) -> TokenResponse:
    repo = OAuthCodeRepository(session)
    record = await repo.find_by_hash(_hash(code))
    if record is None:
        raise AppError("oauth.invalid_grant", "授权码无效")
    if record.used_at is not None:
        raise AppError("oauth.invalid_grant", "授权码已使用")
    if _aware(record.expires_at) <= _now():
        raise AppError("oauth.invalid_grant", "授权码已过期")
    if record.client_id != client_id or record.redirect_uri != redirect_uri:
        raise AppError("oauth.invalid_grant", "client_id/redirect_uri 不匹配")
    if not _verify_pkce(code_verifier, record.code_challenge):
        raise AppError("oauth.invalid_grant", "PKCE 校验失败")
    record.used_at = _now()
    return await _issue_tokens(
        session,
        record.tenant_id,
        record.client_id,
        record.user_id,
        list(record.scopes or []),
        settings,
    )


async def refresh_tokens(
    session: AsyncSession,
    *,
    refresh_token: str,
    client_id: str,
    settings: Settings,
) -> TokenResponse:
    repo = OAuthTokenRepository(session)
    record = await repo.find_by_refresh_hash(_hash(refresh_token))
    if record is None or record.revoked_at is not None:
        raise AppError("oauth.invalid_grant", "refresh token 无效")
    if _aware(record.refresh_expires_at) <= _now():
        raise AppError("oauth.invalid_grant", "refresh token 已过期")
    if record.client_id != client_id:
        raise AppError("oauth.invalid_grant", "client_id 不匹配")
    record.revoked_at = _now()  # refresh rotation:旧 token 一次性
    return await _issue_tokens(
        session,
        record.tenant_id,
        record.client_id,
        record.user_id,
        list(record.scopes or []),
        settings,
    )


async def introspect(session: AsyncSession, token: str) -> OAuthIntrospectRead:
    record = await OAuthTokenRepository(session).find_by_access_hash(_hash(token))
    if record is None or record.revoked_at is not None:
        return OAuthIntrospectRead(active=False)
    expires_at = _aware(record.access_expires_at)
    if expires_at <= _now():
        return OAuthIntrospectRead(active=False)
    return OAuthIntrospectRead(
        active=True,
        tenant_id=record.tenant_id,
        client_id=record.client_id,
        scope=" ".join(record.scopes or []),
        exp=int(expires_at.timestamp()),
    )
