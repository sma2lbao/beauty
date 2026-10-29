"""AppContainer 的认证与预算装配(自 container.py 拆出):api key 校验器、
OAuth 内省器、token 预算、配额快照与用量上报。
"""

from __future__ import annotations

from agent_orchestrator.services import quota as quota_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.mcp_server import (
    ApiKeyVerifier,
    OAuthTokenIntrospector,
)


class AuthAssemblyMixin:
    """认证/预算装配扩展:由 AppContainer 混入。"""

    async def _assemble_auth(self) -> None:
        settings = self.settings
        key_verifier = self.api_key_verifier
        owns_key_verifier = False
        if key_verifier is None and settings.mcp_server_enabled:
            key_verifier = ApiKeyVerifier(
                base_url=settings.account_service_url,
                internal_secret=settings.internal_auth_secret,
            )
            owns_key_verifier = True
        self._publish("api_key_verifier", key_verifier)
        if owns_key_verifier and key_verifier is not None:
            self._closers.append(("api key verifier", key_verifier.aclose))

        introspector = self.oauth_introspector
        owns_introspector = False
        if introspector is None and settings.mcp_server_enabled:
            introspector = OAuthTokenIntrospector(
                base_url=settings.account_service_url,
                internal_secret=settings.internal_auth_secret,
            )
            owns_introspector = True
        self._publish("oauth_introspector", introspector)
        if owns_introspector and introspector is not None:
            self._closers.append(("oauth introspector", introspector.aclose))

    def _assemble_budget(self) -> None:
        settings = self.settings
        # token 预算(P0-3):进程级固定,0 = 不限制
        usage_service.configure_budget(settings.run_token_budget)

        # 配额快照(P0-2):建 run 前读 account 的租户配额;密钥缺失即关闭
        if quota_service.client() is None and quota_service.configure_client(
            base_url=settings.account_service_url,
            internal_secret=settings.internal_auth_secret,
        ):
            self._closers.append(("quota client", quota_service.close_client))

        # 用量上报(P0-1):密钥缺失即关闭(不裸调),测试默认零网络
        if usage_service.reporter() is None and usage_service.configure_reporter(
            base_url=settings.account_service_url,
            internal_secret=settings.internal_auth_secret,
        ):
            self._closers.append(("usage reporter", usage_service.close_reporter))
