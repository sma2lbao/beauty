"""服务间 token:经内部 issue 端点按租户换 access token,进程内缓存。

配套 account-service 的 dev IdP 桩(架构 §5):下游服务开启鉴权后,
服务间调用也走**标准 JWT**(与用户面同一个 JwtVerifier,不设后门)。
orchestrator 作为内部调用发起方,用共享内部密钥向 account 换"以目标
租户为 claim"的 token,按租户缓存到剩余 TTL 不足 60s 时刷新。

阶段 B 替换:mTLS / 服务身份 / OBO 转发用户 token——三个下游客户端的
注入点(`token_provider`)不变,只换这里的实现。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from common.errors import UpstreamError

REFRESH_MARGIN_SECONDS = 60  # 剩余寿命低于该值就提前换新,避免临界过期


class TokenBroker:
    """按租户获取服务间 access token(httpx 客户端,测试可注入 transport)。"""

    def __init__(
        self,
        *,
        base_url: str,
        internal_secret: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._secret = internal_secret
        self._cache: dict[str, tuple[str, float]] = {}  # tenant -> (token, 到期时刻)

    async def token(self, tenant_id: str) -> str:
        """取该租户的服务间 token(命中未过期缓存则零请求)。"""
        cached = self._cache.get(tenant_id)
        now = time.monotonic()
        if cached and cached[1] - now > REFRESH_MARGIN_SECONDS:
            return cached[0]
        data = await self._issue(tenant_id)
        expires_in = int(data.get("expires_in") or 0)
        token = str(data.get("access_token") or "")
        self._cache[tenant_id] = (token, now + max(expires_in, 0))
        return token

    async def _issue(self, tenant_id: str) -> dict[str, Any]:
        try:
            resp = await self._client.post(
                "/v1/auth/issue",
                json={"tenant_id": tenant_id, "subject": "svc-orchestrator"},
                headers={"X-Internal-Secret": self._secret},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "auth.token_broker_unavailable", f"签发服务不可达:{exc}"
            ) from exc
        if resp.status_code != 200:
            raise UpstreamError(
                "auth.token_broker_error",
                f"签发服务返回 {resp.status_code}",
                details={"body": resp.text[:200]},
            )
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()
