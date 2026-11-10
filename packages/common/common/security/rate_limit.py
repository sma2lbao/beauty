"""限流原语:进程内滑动窗口(单副本回退)+ Redis 固定窗口(多副本一致)。

未配置 REDIS_URL 时用 SlidingWindowLimiter(单进程计数、重启清零);配置后由
build_rate_limiter 装配 Redis 后端,多副本共享同一窗口计数。取舍见
docs/design/rate-limiting.md。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from redis.asyncio import from_url

from common.logging import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = get_logger("rate_limit")


class SlidingWindowLimiter:
    """异步滑动窗口:按 key 记录命中时间戳,窗口内超限即拒绝。

    协程间用 asyncio.Lock 串行化,同一实例只应在单个事件循环内使用。
    """

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    async def aclose(self) -> None:
        """进程内实现没有外部资源;与 Redis 版本保持统一接口,便于 lifespan 回收。"""


class RateLimitStore(Protocol):
    """限流计数后端:只暴露"自增并返回窗口内计数"一个动作。"""

    async def increment(self, key: str, ttl_seconds: int) -> int:
        """把 key 的窗口计数 +1 并返回新值;后端必须保证键在 ttl 后过期。"""


class RedisRateLimitStore:
    """Redis 计数存储:INCR + EXPIRE 放同一条 pipeline 原子落库。"""

    def __init__(self, client: Redis, *, namespace: str) -> None:
        self._client = client
        self._namespace = namespace

    async def increment(self, key: str, ttl_seconds: int) -> int:
        # namespace 为空时不加前缀(调用方可能已把 namespace 拼进 key)
        redis_key = f"{self._namespace}:{key}" if self._namespace else key
        # 事务型 pipeline:INCR 与 EXPIRE 同批发送,避免"计数已加、TTL 未设"
        # 留下永不过期的键(无限期封禁)。计数 >1 时重复 EXPIRE 只是刷新 TTL。
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, ttl_seconds)
            count, _ = await pipe.execute()
        return int(count)

    async def aclose(self) -> None:
        """关闭内部连接池(build_rate_limiter 建的 client 归本对象所有)。"""
        await self._client.aclose()


class RedisFixedWindowLimiter:
    """Redis 固定窗口限流:跨进程/副本共享计数,后端故障时 fail-open。

    窗口按 clock() 对齐切桶(固定窗口而非滑动窗口):桶边界处最坏放行 2×limit,
    换来每次请求一次 Redis 往返和一个原子计数键;可注入 clock 便于测试。
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        *,
        store: RateLimitStore,
        namespace: str = "rl",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._store = store
        self._namespace = namespace
        self._clock = clock

    def _bucket_key(self, bucket: int, key: str) -> str:
        prefix = f"{self._namespace}:" if self._namespace else ""
        return f"{prefix}{bucket}:{key}"

    async def allow(self, key: str) -> bool:
        bucket = int(self._clock() // self.window)
        # TTL 取 2 个窗口:桶键在窗口结束后自然过期,即使 EXPIRE 失败也不会
        # 变成无 TTL 的永久键(重启/时钟回拨时最多多封一个窗口)。
        ttl = max(1, int(self.window * 2))
        try:
            count = await self._store.increment(
                self._bucket_key(bucket, key), ttl_seconds=ttl
            )
        except Exception:  # noqa: BLE001 - 后端故障不应把请求全部拒绝
            # fail-open:限流是保护手段而非业务前置条件,Redis 不可用时可用性优先。
            log.warning(
                "rate_limit.backend_error",
                namespace=self._namespace,
                key=key,
                exc_info=True,
            )
            return True
        return count <= self.limit

    async def aclose(self) -> None:
        """回收后端资源(store 若自持连接池)。"""
        close = getattr(self._store, "aclose", None)
        if close is not None:
            await close()


def build_rate_limiter(
    *,
    limit: int,
    window_seconds: float = 60.0,
    redis_url: str | None,
    namespace: str,
) -> SlidingWindowLimiter | RedisFixedWindowLimiter:
    """按 REDIS_URL 选限流后端;返回对象统一提供 async aclose()。

    redis_url 为空:进程内滑动窗口(单副本/开发默认);非空:Redis 固定窗口。
    from_url 只建连接池、不发网络,首个请求才真正连接 Redis。
    """
    if not redis_url:
        return SlidingWindowLimiter(limit, window_seconds=window_seconds)
    client = from_url(redis_url, decode_responses=True)
    # namespace 由 limiter 拼进桶键(f"{namespace}:{bucket}:{key}"),store 只做计数,
    # 不再重复加前缀;这样 fail-open warning 里的 namespace 也是可读的服务名。
    store = RedisRateLimitStore(client, namespace="")
    return RedisFixedWindowLimiter(
        limit,
        window_seconds=window_seconds,
        store=store,
        namespace=namespace,
    )
