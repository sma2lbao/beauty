"""GitHub 内置 provider(GitHub App,user-to-server token)。

设计约束见 docs/design/connector.md §8.5 C4.1。三个 provider 特有的取舍:

- 授权时带 scope=offline_access:即使 App 没开「Expire user authorization tokens」,
  GitHub 也会返回 8h access token + refresh token,平台才能统一走刷新链路。
- refresh token 是轮换的:refresh() 返回的新 refresh token 由服务层同事务写回,
  provider 内不做缓存/重试。
- 静态令牌(PAT)没有 refresh token:refresh() 原样返回,避免 24h 巡检把它置过期;
  fetch_identity 也不对 PAT 调 /user/installations(该端点只接受 GitHub App token)。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx

from connector.core.clock import utcnow
from connector.providers.base import (
    BaseProvider,
    Credentials,
    Identity,
    ToolSpec,
)
from connector.providers.errors import ProviderError
from connector.providers.github_api import (
    API_BASE,
    AUTH_BASE,
    _HttpMixin,
)
from connector.providers.github_tools import TOOLS, _ToolsMixin

#: GitHub 官方文档:refresh_token_expires_in 恒为 6 个月(秒)。
REFRESH_TOKEN_TTL_SECONDS = 15_897_600


class GithubProvider(_ToolsMixin, _HttpMixin, BaseProvider):
    name = "github"
    prefix = "Github"
    display_name = "GitHub"
    description = (
        "GitHub App 授权:读写仓库、issue 与 pull request;"
        "也支持粘贴个人访问令牌(PAT)快速连接。"
    )
    auth_kind = "user"
    token_connectable = True
    docs_url = (
        "https://docs.github.com/en/apps/creating-github-apps/"
        "authenticating-with-a-github-app/refreshing-user-access-tokens"
    )
    tools: ClassVar[tuple[ToolSpec, ...]] = TOOLS

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.github_client_id and self.settings.github_client_secret
        )

    def scopes(self) -> tuple[str, ...]:
        return ("offline_access",)

    def authorize_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        query = urlencode(
            {
                "client_id": self.settings.github_client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": "offline_access",
            }
        )
        return f"{AUTH_BASE}/login/oauth/authorize?{query}"

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
                "client_id": self.settings.github_client_id,
                "client_secret": self.settings.github_client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            refresh=False,
        )
        return _credentials_from(payload)

    async def refresh(
        self,
        credentials: Credentials,
        client: httpx.AsyncClient,
    ) -> Credentials:
        if credentials.refresh_token is None:
            # PAT/静态令牌没有可轮换的 refresh token;巡检不应因此销毁连接。
            return credentials
        payload = await self._token_request(
            client,
            {
                "client_id": self.settings.github_client_id,
                "client_secret": self.settings.github_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": credentials.refresh_token,
            },
            refresh=True,
        )
        return _credentials_from(payload)

    async def fetch_identity(
        self,
        credentials: Credentials,
        client: httpx.AsyncClient,
    ) -> Identity:
        user = await self._get_json(client, f"{API_BASE}/user", credentials)
        login = user.get("login") if isinstance(user, dict) else None
        user_id = user.get("id") if isinstance(user, dict) else None
        if not isinstance(login, str) or not login or user_id is None:
            raise ProviderError("connector.upstream_error", "GitHub 用户信息不完整")
        metadata: dict[str, Any] = {}
        if credentials.refresh_token is not None:
            metadata = await self._installations(client, credentials)
        return Identity(
            account=login,
            display_name=(
                user.get("name") if isinstance(user.get("name"), str) else None
            )
            or login,
            provider_account_id=str(user_id),
            metadata=metadata,
        )

    async def _installations(
        self,
        client: httpx.AsyncClient,
        credentials: Credentials,
    ) -> dict[str, Any]:
        try:
            data = await self._get_json(
                client,
                f"{API_BASE}/user/installations",
                credentials,
                params={"per_page": 100},
            )
        except ProviderError as exc:
            # 授权成功但 App 未安装时 GitHub 仍可能返回空列表;真正的 403
            # 不应阻断连接创建,只把「无法判断安装状态」写进 metadata。
            return {
                "installations": [],
                "has_installation": None,
                "installations_error": exc.code,
            }
        items = data.get("installations") if isinstance(data, dict) else None
        installations: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            account = (
                item.get("account") if isinstance(item.get("account"), dict) else {}
            )
            installations.append(
                {
                    "login": account.get("login"),
                    "id": item.get("id"),
                }
            )
        total = data.get("total_count") if isinstance(data, dict) else None
        if not isinstance(total, int):
            total = len(installations)
        return {
            "installations": installations,
            "has_installation": total > 0,
            "installations_total": total,
        }

    async def connect_with_token(
        self, token: str, client: httpx.AsyncClient
    ) -> tuple[Credentials, Identity]:
        if not isinstance(token, str) or not token.strip():
            raise ProviderError("connector.invalid_credentials", "令牌不能为空")
        access_token = token.strip()
        credentials = Credentials(access_token=access_token)
        try:
            user = await self._get_json(client, f"{API_BASE}/user", credentials)
        except ProviderError as exc:
            if exc.code in {
                "connector.auth_expired",
                "connector.invalid_credentials",
            }:
                raise ProviderError(
                    "connector.invalid_credentials",
                    "GitHub 令牌无效",
                    hint="确认 PAT 未过期且具备所需权限",
                ) from exc
            raise
        login = user.get("login") if isinstance(user, dict) else None
        user_id = user.get("id") if isinstance(user, dict) else None
        if not isinstance(login, str) or not login:
            raise ProviderError("connector.invalid_credentials", "GitHub 令牌无效")
        return Credentials(
            access_token=access_token,
            token_type="Bearer",
            expires_at=None,
        ), Identity(
            account=login,
            display_name=(
                user.get("name") if isinstance(user.get("name"), str) else None
            )
            or login,
            provider_account_id=(str(user_id) if user_id is not None else None),
            metadata={"connect": "token"},
        )


def _credentials_from(payload: dict[str, Any]) -> Credentials:
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if (
        not isinstance(access_token, str)
        or not access_token
        or not isinstance(refresh_token, str)
        or not refresh_token
        or not isinstance(expires_in, int)
        or expires_in <= 0
    ):
        raise ProviderError(
            "connector.oauth_failed",
            "GitHub App 未返回可刷新的令牌",
            hint="GitHub App 未开启 Expire user authorization tokens",
        )
    now = utcnow()
    refresh_expires_in = payload.get("refresh_token_expires_in")
    if not isinstance(refresh_expires_in, int) or refresh_expires_in <= 0:
        refresh_expires_in = REFRESH_TOKEN_TTL_SECONDS
    scope = payload.get("scope")
    token_type = payload.get("token_type")
    return Credentials(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=(
            token_type if isinstance(token_type, str) and token_type else "Bearer"
        ),
        scope=scope if isinstance(scope, str) else "",
        expires_at=now + timedelta(seconds=expires_in),
        refresh_expires_at=now + timedelta(seconds=refresh_expires_in),
    )
