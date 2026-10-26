"""BYOK 密钥解析:按租户向 account-service 换提供商密钥(缓存 + 回退平台 key)。

网关仍是全平台唯一模型出口,但密钥归属下放到租户:调用方带 X-Tenant-Id,
这里向 account-service 的内部端点 GET /v1/byok/{provider}/secret 换 api_key
(共享密钥 X-Internal-Secret 保护)。解析失败绝不阻塞调用——统一返回 None,
由服务层回退平台 key(LLM_GATEWAY__API_KEY),保证"BYOK 不可用 ≠ 全平台不可用"。

缓存:
- 正缓存 byok_cache_ttl_seconds(默认 300s),负缓存(404=租户没配)用更短的
  byok_negative_cache_ttl_seconds(默认 30s);
- 非 200/超时/网络错误不缓存——下一请求立即重试,避免短故障被放大;
- 单把 asyncio.Lock 串行化未命中解析(防并发击穿),缓存按
  byok_cache_max_entries 上限做最近使用淘汰(LRU)。

日志 byok_resolved 只落 tenant/provider/key_hint/source——api_key 永不进日志;
指标 llm_byok_lookups_total{status} 记 disabled/negative/error/byok/cache_hit。
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
from common.logging import get_logger
from common.middleware import inject_request_headers
from common.security import SECRET_HEADER, TENANT_HEADER

from llm_gateway import metrics
from llm_gateway.core.config import Settings

#: 缓存未命中的哨兵(区别于"命中了负缓存"的 None)
_MISS = object()


@dataclass
class ResolvedKey:
    """一次成功解析的租户密钥;api_key 不参与 repr(避免误打进日志)。"""

    api_key: str = field(repr=False)
    source: str = "byok"
    provider: str = ""
    key_hint: str = ""
    key_id: str = ""


class ByokResolver:
    """租户级密钥解析器(TTL 正/负缓存 + 并发防击穿 + 失败回退 None)。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        # 懒建:AsyncClient 绑定事件循环,延后到首次解析(应用 lifespan 内)
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        # (tenant, provider) -> (密钥或 None, 到期时刻 monotonic)
        self._cache: OrderedDict[tuple[str, str], tuple[ResolvedKey | None, float]] = (
            OrderedDict()
        )
        self._log = get_logger("llm_gateway.byok")

    def _enabled(self) -> bool:
        return bool(self._settings.byok_enabled and self._settings.internal_secret)

    async def resolve(self, tenant_id: str | None, provider: str) -> ResolvedKey | None:
        """按租户解析密钥;未启用/无租户/未配置都回 None,由调用方回退平台 key。"""
        if not self._enabled() or not tenant_id or not provider:
            metrics.observe_byok_lookup(status="disabled")
            return None
        cache_key = (tenant_id, provider)
        cached = self._cache_get(cache_key)
        if cached is not _MISS:
            metrics.observe_byok_lookup(status="cache_hit")
            if cached is not None:
                self._log_resolved(tenant_id, cached)
            return cached
        # 未命中:同一时刻只允许一个请求穿透(其余在锁上等,醒来先看缓存)
        async with self._lock:
            cached = self._cache_get(cache_key)
            if cached is not _MISS:
                metrics.observe_byok_lookup(status="cache_hit")
                if cached is not None:
                    self._log_resolved(tenant_id, cached)
                return cached
            key, status = await self._fetch(tenant_id, provider)
            metrics.observe_byok_lookup(status=status)
            if status == "byok" and key is not None:
                self._store(cache_key, key, self._settings.byok_cache_ttl_seconds)
                self._log_resolved(tenant_id, key)
            elif status == "negative":
                # 租户没配这个 provider:短缓存,避免每请求都打 account
                self._store(
                    cache_key, None, self._settings.byok_negative_cache_ttl_seconds
                )
            return key

    def _cache_get(self, cache_key: tuple[str, str]) -> ResolvedKey | None | object:
        """取缓存:未命中/过期返回 _MISS(值本身可以是 None = 负缓存)。"""
        entry = self._cache.get(cache_key)
        if entry is None:
            return _MISS
        key, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._cache[cache_key]
            return _MISS
        self._cache.move_to_end(cache_key)
        return key

    def _store(
        self,
        cache_key: tuple[str, str],
        key: ResolvedKey | None,
        ttl_seconds: float,
    ) -> None:
        self._cache[cache_key] = (
            key,
            time.monotonic() + max(float(ttl_seconds), 0.0),
        )
        self._cache.move_to_end(cache_key)
        max_entries = max(int(self._settings.byok_cache_max_entries), 1)
        while len(self._cache) > max_entries:
            self._cache.popitem(last=False)

    async def _fetch(
        self, tenant_id: str, provider: str
    ) -> tuple[ResolvedKey | None, str]:
        """一次 account 解析,返回 (key, status);异常一律降级为 (None, error)。"""
        settings = self._settings
        url = f"/v1/byok/{quote(provider, safe='')}/secret"
        try:
            resp = await self._client_or_create().get(
                url,
                headers={
                    SECRET_HEADER: settings.internal_secret,
                    TENANT_HEADER: tenant_id,
                },
            )
        except httpx.HTTPError as exc:
            self._log.warning(
                "byok_lookup_failed",
                tenant=tenant_id,
                provider=provider,
                reason=type(exc).__name__,
                detail=str(exc),
            )
            return None, "error"
        if resp.status_code == 404:
            # 租户没配该 provider 的 key:正常回退,不告警
            return None, "negative"
        if resp.status_code != 200:
            self._log.warning(
                "byok_lookup_failed",
                tenant=tenant_id,
                provider=provider,
                status=resp.status_code,
                detail=resp.text[:200],
            )
            return None, "error"
        try:
            payload = resp.json()
        except ValueError:
            self._log.warning(
                "byok_lookup_failed",
                tenant=tenant_id,
                provider=provider,
                reason="bad_json",
            )
            return None, "error"
        data = payload if isinstance(payload, dict) else {}
        api_key = str(data.get("api_key") or "")
        if not api_key:
            self._log.warning(
                "byok_lookup_failed",
                tenant=tenant_id,
                provider=provider,
                reason="missing_api_key",
            )
            return None, "error"
        return (
            ResolvedKey(
                api_key=api_key,
                source="byok",
                provider=provider,
                key_hint=str(data.get("key_hint") or ""),
                key_id=str(data.get("key_id") or ""),
            ),
            "byok",
        )

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.account_base_url.rstrip("/"),
                timeout=self._settings.account_timeout_seconds,
                transport=self._transport,
                event_hooks={"request": [inject_request_headers]},
            )
        return self._client

    def _log_resolved(self, tenant_id: str, key: ResolvedKey) -> None:
        """成功解析的审计日志:不含 api_key,只留可关联的提示信息。"""
        self._log.info(
            "byok_resolved",
            tenant=tenant_id,
            provider=key.provider,
            key_hint=key.key_hint,
            source=key.source,
        )

    async def aclose(self) -> None:
        """关闭连接池(应用 shutdown);未用过则 no-op。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
