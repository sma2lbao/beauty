"""配额快照(P0-2 前置闸):建 run 前向 account-service 取租户配额。

契约:``GET /v1/usage/quota``(``X-Internal-Secret`` + ``X-Tenant-Id``)返回
``{monthly_tokens, max_concurrent_runs, used_tokens}``,``None`` 表示该维度不限制。

任何失败(未配置密钥/网络/非 200/坏 JSON)都按「未配置」处理并打 warning:
配额是保护性限流,不是鉴权——account 抖动不能让任务创建整体挂掉。
判定本身留在 ``services.runs``(仓储计数需要 DB session),这里只取数。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from common.security import SECRET_HEADER
from common.logging import get_logger
from common.middleware import inject_request_headers

log = get_logger(__name__)

DEFAULT_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class QuotaSnapshot:
    """租户配额 + 本月已用量;None 维度表示不限制。"""

    monthly_tokens: int | None
    max_concurrent_runs: int | None
    used_tokens: int

    @property
    def monthly_tokens_exceeded(self) -> bool:
        return (
            self.monthly_tokens is not None and self.used_tokens >= self.monthly_tokens
        )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None


def _parse(payload: dict[str, Any]) -> QuotaSnapshot:
    return QuotaSnapshot(
        monthly_tokens=_int_or_none(payload.get("monthly_tokens")),
        max_concurrent_runs=_int_or_none(payload.get("max_concurrent_runs")),
        used_tokens=int(payload.get("used_tokens") or 0),
    )


class QuotaClient:
    """取配额快照:短 TTL 进程内缓存(建 run 是低频写操作,30s 足够)。

    缓存的意义是省掉一次 RPC 而非保证强一致——并发数每次都用 DB 实时计数。
    """

    def __init__(
        self,
        *,
        base_url: str,
        internal_secret: str,
        timeout_seconds: float = 3.0,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            # 与其它内部调用一致:request_id / traceparent 跨服务透传
            event_hooks={"request": [inject_request_headers]},
        )
        self._secret = internal_secret
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, QuotaSnapshot]] = {}

    async def fetch(self, tenant_id: str) -> QuotaSnapshot | None:
        now = time.monotonic()
        cached = self._cache.get(tenant_id)
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]
        try:
            response = await self._client.get(
                "/v1/usage/quota",
                headers={
                    SECRET_HEADER: self._secret,
                    "X-Tenant-Id": tenant_id,
                },
            )
        except httpx.HTTPError as exc:
            log.warning("quota_fetch_failed", tenant_id=tenant_id, detail=str(exc))
            return None
        except Exception as exc:  # noqa: BLE001 - 限流取数任何异常都 fail-open
            log.warning("quota_fetch_failed", tenant_id=tenant_id, detail=str(exc))
            return None
        if response.status_code != 200:
            log.warning(
                "quota_fetch_failed", tenant_id=tenant_id, status=response.status_code
            )
            return None
        try:
            snapshot = _parse(response.json())
        except (ValueError, TypeError) as exc:
            log.warning(
                "quota_fetch_failed", tenant_id=tenant_id, detail=f"bad payload:{exc}"
            )
            return None
        self._cache[tenant_id] = (now, snapshot)
        return snapshot

    async def aclose(self) -> None:
        await self._client.aclose()


_client: QuotaClient | None = None


def configure_client(
    *,
    base_url: str,
    internal_secret: str,
    timeout_seconds: float = 3.0,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> QuotaClient | None:
    """装配进程级客户端;base_url/密钥缺失时返回 None(不裸调)。"""
    global _client
    if not base_url or not internal_secret:
        _client = None
        return None
    _client = QuotaClient(
        base_url=base_url,
        internal_secret=internal_secret,
        timeout_seconds=timeout_seconds,
        ttl_seconds=ttl_seconds,
        transport=transport,
    )
    return _client


def client() -> QuotaClient | None:
    return _client


async def close_client() -> None:
    """关闭并清空进程级客户端(应用 shutdown 用,避免悬空的已关闭连接)。"""
    global _client
    if _client is not None:
        client = _client
        _client = None
        await client.aclose()


async def fetch(tenant_id: str) -> QuotaSnapshot | None:
    """未装配客户端时返回 None(=不限制),调用方据此跳过准入判断。"""
    if _client is None:
        return None
    return await _client.fetch(tenant_id)
