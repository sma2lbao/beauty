"""Gmail / Microsoft 365 OAuth 连接(M3 阶段 3A)。

授权码回调用签名 state(tenant/provider/ts)防伪造;token 只存加密凭证,
读端不返回。Gmail/Graph 的实际调用在 mail.py 的 OAuth transport 里。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from common.errors import AppError, UpstreamError
from sqlalchemy.ext.asyncio import AsyncSession

from connector.core.config import Settings
from connector.core.crypto import encrypt_credentials
from connector.models.connections import Connection
from connector.repositories import ConnectionRepository
from connector.schemas.connections import ConnectionStatus

STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    scopes: list[str]
    redirect_path: str


PROVIDERS: dict[str, ProviderConfig] = {
    "google": ProviderConfig(
        name="google",
        client_id="",
        client_secret="",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=[
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.modify",
        ],
        redirect_path="google/callback",
    ),
    "microsoft": ProviderConfig(
        name="microsoft",
        client_id="",
        client_secret="",
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        scopes=[
            "openid",
            "email",
            "profile",
            "offline_access",
            "https://graph.microsoft.com/Mail.ReadWrite",
            "https://graph.microsoft.com/Mail.Send",
        ],
        redirect_path="microsoft/callback",
    ),
}


def provider_config(provider: str, settings: Settings) -> ProviderConfig:
    base = PROVIDERS.get(provider)
    if base is None:
        raise AppError("connector.provider_unsupported", f"不支持的提供商:{provider}")
    if provider == "google":
        client_id, secret = settings.google_client_id, settings.google_client_secret
    else:
        client_id = settings.microsoft_client_id
        secret = settings.microsoft_client_secret
    if not client_id or not secret:
        raise AppError(
            "connector.oauth_disabled",
            f"{provider} OAuth 未配置(client id/secret 为空)",
        )
    return ProviderConfig(
        name=base.name,
        client_id=client_id,
        client_secret=secret,
        authorize_url=base.authorize_url,
        token_url=base.token_url,
        scopes=list(base.scopes),
        redirect_path=base.redirect_path,
    )


def _secret(settings: Settings) -> str:
    return settings.oauth_state_secret or settings.credential_secret


def _sign(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _signature(raw: str, settings: Settings) -> str:
    digest = hmac.new(
        _secret(settings).encode(), raw.encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def make_state(provider: str, tenant_id: str, settings: Settings) -> str:
    import secrets

    raw = _sign(
        {
            "tenant_id": tenant_id,
            "provider": provider,
            "ts": int(time.time()),
            "nonce": secrets.token_urlsafe(16),
        }
    )
    return raw + "." + _signature(raw, settings)


def verify_state(state: str, settings: Settings) -> dict:
    raw, _, signature = state.partition(".")
    if not raw or not signature or not hmac.compare_digest(
        signature, _signature(raw, settings)
    ):
        raise AppError("connector.oauth_state_invalid", "OAuth state 无效")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception as exc:  # noqa: BLE001
        raise AppError("connector.oauth_state_invalid", "OAuth state 无效") from exc
    if int(time.time()) - int(payload.get("ts", 0)) > STATE_TTL_SECONDS:
        raise AppError("connector.oauth_state_expired", "OAuth state 已过期")
    return payload


def redirect_uri(provider: str, settings: Settings) -> str:
    base = settings.oauth_redirect_base.rstrip("/")
    return base + "/" + PROVIDERS[provider].redirect_path


def authorize_url(provider: str, tenant_id: str, settings: Settings) -> str:
    config = provider_config(provider, settings)
    params = {
        "client_id": config.client_id,
        "redirect_uri": redirect_uri(provider, settings),
        "response_type": "code",
        "scope": " ".join(config.scopes),
        "state": make_state(provider, tenant_id, settings),
        "access_type": "offline",
        "prompt": "consent",
    }
    return config.authorize_url + "?" + urlencode(params)


async def _exchange_code(
    provider: str,
    code: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
) -> dict:
    config = provider_config(provider, settings)
    async with httpx.AsyncClient(
        timeout=15.0, transport=transport
    ) as client:
        response = await client.post(
            config.token_url,
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri(provider, settings),
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        raise UpstreamError(
            "connector.oauth_exchange_failed",
            f"{provider} token 交换失败 {response.status_code}",
            details={"body": response.text[:200]},
        )
    return response.json()


async def _fetch_email(
    provider: str,
    access_token: str,
    transport: httpx.AsyncBaseTransport | None,
) -> str:
    async with httpx.AsyncClient(
        timeout=15.0, transport=transport
    ) as client:
        if provider == "google":
            response = await client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code != 200:
                raise UpstreamError(
                    "connector.oauth_profile_failed", "读取 Google 账号失败"
                )
            body = response.json()
            return str(body.get("email") or "")
        response = await client.get(
            "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            raise UpstreamError(
                "connector.oauth_profile_failed", "读取 Microsoft 账号失败"
            )
        body = response.json()
        return str(body.get("mail") or body.get("userPrincipalName") or "")


async def complete_oauth(
    session: AsyncSession,
    provider: str,
    code: str,
    state: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[Connection, bool]:
    payload = verify_state(state, settings)
    if payload.get("provider") != provider:
        raise AppError("connector.oauth_state_invalid", "OAuth state provider 不匹配")
    tenant_id = str(payload["tenant_id"])
    tokens = await _exchange_code(provider, code, settings, transport)
    access_token = str(tokens.get("access_token") or "")
    refresh_token = tokens.get("refresh_token")
    email = await _fetch_email(provider, access_token, transport)
    if not email:
        raise AppError("connector.oauth_email_missing", "OAuth 账号未返回邮箱")
    stored_provider = "gmail" if provider == "google" else "outlook"
    credentials = {
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + int(tokens.get("expires_in") or 3600),
    }
    connections = ConnectionRepository(session)
    existing = await connections.find_by_account(
        tenant_id, provider=stored_provider, account=email
    )
    if existing is None:
        existing = Connection(
            tenant_id=tenant_id,
            provider=stored_provider,
            account=email,
            display_name=f"{'Google' if provider == 'google' else 'Microsoft'} 邮箱",
            status=ConnectionStatus.ACTIVE.value,
            credentials=encrypt_credentials(settings.credential_secret, credentials),
        )
        await connections.add(existing)
        return existing, True
    existing.status = ConnectionStatus.ACTIVE.value
    existing.credentials = encrypt_credentials(
        settings.credential_secret, credentials
    )
    return existing, False
