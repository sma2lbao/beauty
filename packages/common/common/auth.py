"""JWT 鉴权基座(架构 §10):IdP(Logto)签发的 access token 校验与租户提取。

约定:

- 只信任签名 + `iss`/`aud`/`exp` 全部通过的 token;
- 签名密钥从 IdP 的 JWKS 拉取并带 TTL 缓存;遇到未知 kid 会强制刷新一次
  (支持密钥轮换);JWKS 不可达是 502(上游问题),token 有问题才是 401;
- **租户来自 token claim**(默认 `tenant_id`),claim 缺失直接拒绝——不猜测、
  不回退到请求头(避免伪造租户);
- `transport` 可注入:测试用 httpx.MockTransport,零网络。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from common.errors import UnauthorizedError, UpstreamError


@dataclass(frozen=True)
class Principal:
    """已认证的调用方:身份 + 租户 + 授权范围。"""

    subject: str
    tenant_id: str
    scopes: tuple[str, ...]
    raw_claims: dict[str, Any]


class JwtVerifier:
    """IdP access token 校验器(异步,可复用于所有服务)。"""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str | None = None,
        jwks_url: str | None = None,
        tenant_claim: str = "tenant_id",
        algorithms: tuple[str, ...] = ("RS256",),
        leeway_seconds: int = 30,
        jwks_ttl_seconds: int = 300,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._jwks_url = jwks_url
        self._tenant_claim = tenant_claim
        self._algorithms = list(algorithms)
        self._leeway = leeway_seconds
        self._ttl = jwks_ttl_seconds
        self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._discovered_url: str | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def verify(self, token: str) -> Principal:
        """校验 token 并返回 Principal;任何不可信情况都是 401。"""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise UnauthorizedError(
                "auth.token_invalid", f"token 无法解析:{exc}"
            ) from exc
        key = await self._key_for(header.get("kid") or None)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError(
                "auth.token_invalid", f"token 校验失败:{exc}"
            ) from exc
        tenant = claims.get(self._tenant_claim)
        if not isinstance(tenant, str) or not tenant:
            raise UnauthorizedError(
                "auth.tenant_missing",
                f"token 缺少 {self._tenant_claim} claim",
            )
        scopes = claims.get("scope") or claims.get("scopes") or ""
        return Principal(
            subject=str(claims.get("sub") or ""),
            tenant_id=tenant,
            scopes=tuple(str(scopes).split()),
            raw_claims=claims,
        )

    async def _key_for(self, kid: str | None) -> Any:
        async with self._lock:
            if self._is_fresh() and kid and kid in self._keys:
                return self._keys[kid]
            await self._refresh()
            if kid and kid in self._keys:
                return self._keys[kid]
            if kid is None and len(self._keys) == 1:
                # 单密钥 IdP 的 token 可以不带 kid
                return next(iter(self._keys.values()))
        raise UnauthorizedError("auth.key_unknown", f"未知签名密钥(kid={kid})")

    def _is_fresh(self) -> bool:
        return (
            self._fetched_at is not None
            and (time.monotonic() - self._fetched_at) < self._ttl
        )

    async def _refresh(self) -> None:
        url = self._jwks_url or self._discovered_url
        if url is None:
            url = await self._discover_jwks_url()
            self._discovered_url = url
        document = await self._get_json(url, what="IdP 密钥集")
        keys: dict[str, Any] = {}
        for item in document.get("keys") or []:
            kid = item.get("kid")
            if not kid:
                continue
            try:
                keys[str(kid)] = jwt.PyJWK.from_dict(item).key
            except Exception:  # noqa: BLE001  跳过无法解析的键(算法不支持等)
                continue
        if not keys:
            raise UpstreamError("auth.jwks_empty", "IdP 密钥集为空或不可解析")
        self._keys = keys
        self._fetched_at = time.monotonic()

    async def _discover_jwks_url(self) -> str:
        discovery_url = f"{self._issuer}/.well-known/openid-configuration"
        document = await self._get_json(discovery_url, what="IdP 发现文档")
        jwks_uri = document.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise UpstreamError("auth.jwks_missing", "IdP 发现文档缺 jwks_uri")
        return jwks_uri

    async def _get_json(self, url: str, *, what: str) -> dict[str, Any]:
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # IdP 不可达属于上游问题(502),不是调用方凭证问题
            raise UpstreamError(
                "auth.jwks_unavailable", f"无法获取{what}:{exc}"
            ) from exc
        if not isinstance(document, dict):
            raise UpstreamError("auth.jwks_invalid", f"{what}格式不正确")
        return document
