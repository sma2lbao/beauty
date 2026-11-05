"""租户 BYOK 自定义模型目录:向 account-service 取该租户声明的模型清单。

BYOK 不只托管密钥,也托管"这个租户能调哪些模型":租户在 account-service
保存的 models 清单经内部端点 GET /v1/byok/catalog 汇总返回(共享密钥
X-Internal-Secret 保护)。网关据此把自定义模型并入白名单与 GET /v1/models,
否则租户自己开通的模型会被平台白名单拦成 400。

语义与 `byok.py` 的密钥解析保持一致:

- 未启用(`byok_enabled` 关或未配 `internal_secret`)或无租户 = 空列表,
  一个请求都不发(metric status=disabled);
- 非 200/超时/网络错误/坏 JSON 一律降级为空列表且**不缓存**——目录故障
  不能变成"模型全部不可用",也不能把短故障放大成 TTL 内的持续错误;
- 成功才按租户 TTL 缓存(`byok_catalog_cache_ttl_seconds`),命中 metric
  status=hit、未命中请求 status=miss;
- 日志 `byok_catalog_lookup_failed` 只落 tenant/状态码/截断正文,不含密钥。
"""

from __future__ import annotations

import time

import httpx
from common.logging import get_logger
from common.middleware import inject_request_headers
from common.security import SECRET_HEADER, TENANT_HEADER

from llm_gateway import metrics
from llm_gateway.core.config import Settings

#: 缓存未命中的哨兵(缓存值本身可以是空列表,不能拿 [] 判断)
_MISS = object()


class ByokCatalog:
    """租户自定义模型目录客户端(TTL 缓存 + 失败降级为空列表)。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        # 懒建:AsyncClient 绑定事件循环,延后到首次查询(应用 lifespan 内)
        self._client: httpx.AsyncClient | None = None
        # tenant -> (模型列表, 到期时刻 monotonic)
        self._cache: dict[str, tuple[list[str], float]] = {}
        self._log = get_logger("llm_gateway.byok_catalog")

    def _enabled(self) -> bool:
        return bool(self._settings.byok_enabled and self._settings.internal_secret)

    async def custom_models(self, tenant_id: str | None) -> list[str]:
        """取租户自定义模型;未启用/无租户/查询失败都返回空列表(绝不抛错)。"""
        if not self._enabled() or not tenant_id:
            metrics.observe_byok_catalog_lookup(status="disabled")
            return []
        cached = self._cache_get(tenant_id)
        if cached is not _MISS:
            metrics.observe_byok_catalog_lookup(status="hit")
            return list(cached)
        models, ok = await self._fetch(tenant_id)
        if not ok:
            metrics.observe_byok_catalog_lookup(status="error")
            return []
        self._store(tenant_id, models)
        metrics.observe_byok_catalog_lookup(status="miss")
        return models

    def _cache_get(self, tenant_id: str) -> list[str] | object:
        """取缓存:未命中/过期返回 _MISS(过期条目顺手删掉)。"""
        entry = self._cache.get(tenant_id)
        if entry is None:
            return _MISS
        models, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._cache[tenant_id]
            return _MISS
        return models

    def _store(self, tenant_id: str, models: list[str]) -> None:
        self._cache[tenant_id] = (
            list(models),
            time.monotonic()
            + max(float(self._settings.byok_catalog_cache_ttl_seconds), 0.0),
        )

    async def _fetch(self, tenant_id: str) -> tuple[list[str], bool]:
        """一次目录查询,返回 (模型列表, 是否成功);异常一律降级为 ([], False)。"""
        settings = self._settings
        try:
            resp = await self._client_or_create().get(
                "/v1/byok/catalog",
                headers={
                    SECRET_HEADER: settings.internal_secret,
                    TENANT_HEADER: tenant_id,
                },
            )
        except httpx.HTTPError as exc:
            self._log.warning(
                "byok_catalog_lookup_failed",
                tenant=tenant_id,
                reason=type(exc).__name__,
                detail=str(exc),
            )
            return [], False
        if resp.status_code != 200:
            self._log.warning(
                "byok_catalog_lookup_failed",
                tenant=tenant_id,
                status=resp.status_code,
                detail=resp.text[:200],
            )
            return [], False
        try:
            payload = resp.json()
        except ValueError:
            self._log.warning(
                "byok_catalog_lookup_failed",
                tenant=tenant_id,
                reason="bad_json",
            )
            return [], False
        data = payload if isinstance(payload, dict) else {}
        raw = data.get("models")
        if not isinstance(raw, list):
            return [], True
        # 只认非空字符串:上游给 null/数字/空串时丢弃,不把脏值带进白名单
        return [model for model in raw if isinstance(model, str) and model], True

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.account_base_url.rstrip("/"),
                timeout=self._settings.account_timeout_seconds,
                transport=self._transport,
                event_hooks={"request": [inject_request_headers]},
            )
        return self._client

    async def aclose(self) -> None:
        """关闭连接池(应用 shutdown);未用过则 no-op。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
