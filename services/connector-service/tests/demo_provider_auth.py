"""demo provider 的授权 / 身份 / 静态令牌链路(测试专用假上游)。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from connector.core.clock import utcnow
from connector.providers.base import Credentials, Identity
from connector.providers.errors import ProviderError
from tests.demo_provider_http import (
    _AUTH_ERROR_CODES,
    _RATE_LIMIT_CODES,
    _has_error,
    _int_code,
    _json_or_none,
    _retry_after,
)


class _AuthMixin:
    """OAuth 授权码 / 刷新 / /userinfo 身份读取 / 静态令牌接入。"""

    def authorize_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        query = urlencode(
            {
                "client_id": self._app_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.scopes()),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.AUTH_BASE}/authorize?{query}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client: httpx.AsyncClient,
    ) -> Credentials:
        payload = await self._token_request(
            client,
            {
                "grant_type": "authorization_code",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )
        return self._credentials_from_token(payload)

    async def refresh(
        self, credentials: Credentials, client: httpx.AsyncClient
    ) -> Credentials:
        if not credentials.refresh_token:
            raise ProviderError(
                "connector.auth_expired",
                "连接没有 refresh token,需要重新授权",
                hint="请重新连接",
            )
        payload = await self._token_request(
            client,
            {
                "grant_type": "refresh_token",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "refresh_token": credentials.refresh_token,
            },
        )
        return self._credentials_from_token(payload)

    async def _token_request(
        self, client: httpx.AsyncClient, body: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"{self.AUTH_BASE}/oauth/token"
        try:
            response = await client.post(url, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "connector.upstream_timeout",
                "授权服务超时",
                hint="稍后重试",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "connector.upstream_unavailable",
                "授权服务不可达",
                hint="稍后重试",
                retryable=True,
            ) from exc
        payload = _json_or_none(response)
        if response.status_code >= 400 or _has_error(payload):
            raise self._token_error(response, payload)
        return payload or {}

    def _credentials_from_token(self, payload: dict[str, Any]) -> Credentials:
        access_token = payload.get("access_token")
        if not access_token:
            raise ProviderError("connector.oauth_failed", "授权服务未返回 access_token")
        return Credentials(
            access_token=str(access_token),
            refresh_token=payload.get("refresh_token"),
            token_type=str(payload.get("token_type") or "Bearer"),
            scope=str(payload.get("scope") or ""),
            expires_at=_expiry(payload.get("expires_in")),
            refresh_expires_at=_expiry(payload.get("refresh_token_expires_in")),
        )

    def _token_error(
        self, response: httpx.Response, payload: dict[str, Any] | None
    ) -> ProviderError:
        """授权端点的错误分层:限流/上游故障不得被当成"需要重新授权"。"""
        code = _int_code(payload)
        label = code if code is not None else response.status_code
        status = response.status_code
        if status == 429 or code in _RATE_LIMIT_CODES:
            retry_after = _retry_after(response)
            hint = (
                f"授权服务限流,建议 {retry_after} 秒后重试"
                if retry_after
                else "授权服务限流,请稍后重试"
            )
            return ProviderError(
                "connector.upstream_rate_limited",
                f"授权服务限流(代码 {label})",
                hint=hint,
                retryable=True,
            )
        if status >= 500:
            return ProviderError(
                "connector.upstream_unavailable",
                f"授权服务暂不可用(HTTP {status})",
                hint="稍后重试",
                retryable=True,
            )
        if code in _AUTH_ERROR_CODES or status in (400, 401, 403):
            return ProviderError(
                "connector.auth_expired",
                f"授权已失效(代码 {label})",
                hint="请重新连接",
            )
        return ProviderError(
            "connector.oauth_failed",
            f"授权失败(代码 {label})",
            hint="请重新发起授权",
        )

    async def fetch_identity(
        self, credentials: Credentials, client: httpx.AsyncClient
    ) -> Identity:
        data = await self._get(client, f"{self.API_BASE}/userinfo", credentials)
        account_id = data.get("account_id")
        if not account_id:
            raise ProviderError("connector.upstream_error", "上游未返回账号 id")
        return Identity(
            account=str(account_id),
            display_name=data.get("name"),
            provider_account_id=str(account_id),
            external_workspace=data.get("workspace_id"),
            metadata={
                "account_id": account_id,
                "union_id": data.get("union_id"),
                "name": data.get("name"),
                "avatar_url": data.get("avatar_url"),
            },
        )

    async def connect_with_token(
        self, token: str, client: httpx.AsyncClient
    ) -> tuple[Credentials, Identity]:
        """粘贴的静态令牌没有有效期/refresh,先用 /userinfo 校验再落库。

        account 用 "token:<账号 id>",与 OAuth 的 "u-1" 区分:同一账号两种接入方式
        各自一条连接,互不覆盖。
        """
        credentials = Credentials(access_token=token, token_type="Bearer")
        identity = await self.fetch_identity(credentials, client)
        return credentials, Identity(
            account=f"token:{identity.provider_account_id or 'unknown'}",
            display_name=identity.display_name,
            provider_account_id=identity.provider_account_id,
            external_workspace=identity.external_workspace,
            metadata={**identity.metadata, "connect": "token"},
        )


def _expiry(raw: Any):
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return utcnow() + timedelta(seconds=seconds)
