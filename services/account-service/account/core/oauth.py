"""社交 OAuth(Google/GitHub):授权 URL + code 交换 + 用户资料。

- state 用 HS256 自签(oauth_state_secret,回退 internal_secret),携带
  provider/redirect_uri/PKCE verifier,10 分钟过期,不用服务端会话表;
- Google 用 OIDC userinfo,PKCE(S256);GitHub 用 /user + /user/emails;
- 失败统一 AppError,不泄漏 client secret。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from common.errors import AppError, UpstreamError

from account.core.config import Settings

GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_USER = "https://api.github.com/user"
GITHUB_EMAILS = "https://api.github.com/user/emails"


@dataclass(frozen=True)
class OAuthProfile:
    provider: str
    subject: str
    email: str
    name: str | None


def _state_secret(settings: Settings) -> str:
    secret = settings.oauth_state_secret or settings.internal_secret
    if not secret:
        raise AppError(
            "oauth.disabled",
            "OAuth 未配置(需要 OAUTH_STATE_SECRET 或 INTERNAL_SECRET)",
        )
    return secret


def _provider_config(settings: Settings, provider: str) -> tuple[str, str]:
    if (
        provider == "google"
        and settings.google_client_id
        and settings.google_client_secret
    ):
        return settings.google_client_id, settings.google_client_secret
    if (
        provider == "github"
        and settings.github_client_id
        and settings.github_client_secret
    ):
        return settings.github_client_id, settings.github_client_secret
    raise AppError("oauth.provider_disabled", f"{provider} 登录未配置")


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _state_token(
    settings: Settings, provider: str, redirect_uri: str, verifier: str
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "provider": provider,
            "redirect_uri": redirect_uri,
            "verifier": verifier,
            "iat": int(now.timestamp()),
            "exp": int(
                (now + timedelta(seconds=settings.oauth_state_ttl_seconds)).timestamp()
            ),
        },
        _state_secret(settings),
        algorithm="HS256",
    )


def _decode_state(settings: Settings, state: str, provider: str) -> dict[str, str]:
    try:
        payload = jwt.decode(
            state, _state_secret(settings), algorithms=["HS256"], audience=None
        )
    except jwt.PyJWTError as exc:
        raise AppError("oauth.invalid_state", "登录状态无效或已过期") from exc
    if payload.get("provider") != provider:
        raise AppError("oauth.invalid_state", "登录状态与 provider 不匹配")
    return payload


def authorize_url(
    settings: Settings, provider: str, redirect_uri: str
) -> tuple[str, str]:
    client_id, _ = _provider_config(settings, provider)
    verifier, challenge = _pkce()
    state = _state_token(settings, provider, redirect_uri, verifier)
    if provider == "google":
        query = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{GOOGLE_AUTHORIZE}?{urlencode(query)}", state
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
        "state": state,
    }
    return f"{GITHUB_AUTHORIZE}?{urlencode(query)}", state


async def _json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs
) -> dict:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise UpstreamError("oauth.provider_unreachable", "社交登录服务不可达") from exc
    if response.is_error:
        raise UpstreamError(
            "oauth.provider_error", f"社交登录失败({response.status_code})"
        )
    return response.json()


async def _google_profile(
    client: httpx.AsyncClient,
    settings: Settings,
    code: str,
    payload: dict[str, str],
) -> OAuthProfile:
    client_id, client_secret = _provider_config(settings, "google")
    token = await _json(
        client,
        "POST",
        GOOGLE_TOKEN,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": payload["redirect_uri"],
            "code_verifier": payload["verifier"],
        },
    )
    info = await _json(
        client,
        "GET",
        GOOGLE_USERINFO,
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    email = str(info.get("email") or "").lower()
    if not email:
        raise AppError("oauth.email_missing", "Google 账号没有可用邮箱")
    return OAuthProfile("google", str(info["sub"]), email, info.get("name"))


async def _github_profile(
    client: httpx.AsyncClient,
    settings: Settings,
    code: str,
    payload: dict[str, str],
) -> OAuthProfile:
    client_id, client_secret = _provider_config(settings, "github")
    token = await _json(
        client,
        "POST",
        GITHUB_TOKEN,
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": payload["redirect_uri"],
        },
    )
    headers = {
        "Authorization": f"Bearer {token['access_token']}",
        "Accept": "application/vnd.github+json",
    }
    info = await _json(client, "GET", GITHUB_USER, headers=headers)
    email = str(info.get("email") or "").lower()
    if not email:
        emails = await _json(client, "GET", GITHUB_EMAILS, headers=headers)
        verified = [
            item
            for item in emails
            if item.get("verified") and item.get("primary") and item.get("email")
        ]
        email = str(verified[0]["email"]).lower() if verified else ""
    if not email:
        email = f"github-{info['id']}@users.noreply.github.com"
    return OAuthProfile(
        "github", str(info["id"]), email, info.get("name") or info.get("login")
    )


async def exchange(
    client: httpx.AsyncClient,
    settings: Settings,
    provider: str,
    code: str,
    state: str,
) -> OAuthProfile:
    payload = _decode_state(settings, state, provider)
    if not code:
        raise AppError("oauth.code_missing", "缺少授权码")
    if provider == "google":
        return await _google_profile(client, settings, code, payload)
    if provider == "github":
        return await _github_profile(client, settings, code, payload)
    raise AppError("oauth.unknown_provider", "不支持的登录方式")
