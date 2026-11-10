"""限流原语:内存回退、Redis 固定窗口(fake store,零网络)、fail-open。"""

from __future__ import annotations

import pytest

from common.security import rate_limit
from common.security.rate_limit import (
    RedisFixedWindowLimiter,
    RedisRateLimitStore,
    SlidingWindowLimiter,
    build_rate_limiter,
)


class FakeStore:
    """进程内 dict 充当 Redis 计数;可注入故障验证 fail-open。"""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False
        self.closed = False

    async def increment(self, key: str, ttl_seconds: int) -> int:
        if self.fail:
            raise RuntimeError("redis down")
        self.ttls[key] = ttl_seconds
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def aclose(self) -> None:
        self.closed = True


class StoreWithoutClose:
    """只有 increment 的后端(Protocol 只要求这一个方法)。"""

    async def increment(self, key: str, ttl_seconds: int) -> int:
        return 1


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakePipeline:
    """模拟 redis.asyncio Pipeline:execute 落 dict 并返回 [count, expire_ok]。"""

    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._key = ""

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def incr(self, key: str) -> FakePipeline:
        self._key = key
        return self

    def expire(self, key: str, ttl: int) -> FakePipeline:
        self._client.ttls[key] = ttl
        return self

    async def execute(self) -> list[object]:
        data = self._client.data
        data[self._key] = data.get(self._key, 0) + 1
        return [data[self._key], True]


class FakeRedis:
    """只实现 RedisRateLimitStore 用到的 pipeline/aclose。"""

    def __init__(self) -> None:
        self.data: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.closed = False

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction is True
        return FakePipeline(self)

    async def aclose(self) -> None:
        self.closed = True


class CapturingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.calls.append((event, dict(kwargs)))


def _limiter(
    limit: int = 2,
    *,
    store: FakeStore | StoreWithoutClose | None = None,
    namespace: str = "rl",
    clock: Clock | None = None,
) -> RedisFixedWindowLimiter:
    return RedisFixedWindowLimiter(
        limit,
        window_seconds=60.0,
        store=store or FakeStore(),
        namespace=namespace,
        clock=clock or Clock(),
    )


async def test_fixed_window_allows_up_to_limit_then_rejects() -> None:
    limiter = _limiter(limit=2)
    assert await limiter.allow("ip:group") is True
    assert await limiter.allow("ip:group") is True
    assert await limiter.allow("ip:group") is False


async def test_window_rollover_resets_count() -> None:
    clock = Clock(now=1000.0)
    store = FakeStore()
    limiter = _limiter(limit=1, store=store, clock=clock)
    assert await limiter.allow("ip") is True
    assert await limiter.allow("ip") is False
    # 跨过窗口边界 → 新桶键,计数从头开始
    clock.now = 1000.0 + 61.0
    assert await limiter.allow("ip") is True
    assert len(store.counts) == 2


async def test_bucket_key_carries_namespace_bucket_and_key() -> None:
    clock = Clock(now=120.0)
    store = FakeStore()
    limiter = _limiter(store=store, namespace="api-gateway", clock=clock)
    await limiter.allow("account:a:group")
    # 120s // 60s = 桶 2,键前缀带 namespace
    assert list(store.counts) == ["api-gateway:2:account:a:group"]
    assert store.ttls["api-gateway:2:account:a:group"] == 120


async def test_namespace_isolates_counters() -> None:
    store = FakeStore()
    clock = Clock(now=0.0)
    a = _limiter(limit=1, store=store, namespace="svc-a", clock=clock)
    b = _limiter(limit=1, store=store, namespace="svc-b", clock=clock)
    assert await a.allow("ip") is True
    assert await a.allow("ip") is False
    assert await b.allow("ip") is True


async def test_backend_error_fails_open_and_warns(monkeypatch) -> None:
    store = FakeStore()
    store.fail = True
    limiter = _limiter(limit=1, store=store)
    capture = CapturingLogger()
    monkeypatch.setattr(rate_limit, "log", capture)

    assert await limiter.allow("ip") is True
    assert capture.calls
    event, fields = capture.calls[0]
    assert event == "rate_limit.backend_error"
    assert fields["key"] == "ip"


async def test_aclose_releases_backend() -> None:
    store = FakeStore()
    limiter = _limiter(store=store)
    await limiter.aclose()
    assert store.closed is True

    # 后端没有 aclose 时不能抛错(Protocol 只要求 increment)
    await _limiter(store=StoreWithoutClose()).aclose()


async def test_sliding_window_limiter_memory_fallback() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60.0)
    assert await limiter.allow("ip") is True
    assert await limiter.allow("ip") is True
    assert await limiter.allow("ip") is False
    await limiter.aclose()  # 无外部资源


async def test_redis_store_increments_and_expires_atomically() -> None:
    client = FakeRedis()
    store = RedisRateLimitStore(client, namespace="rl")
    assert await store.increment("bucket:key", ttl_seconds=120) == 1
    assert await store.increment("bucket:key", ttl_seconds=120) == 2
    assert client.data == {"rl:bucket:key": 2}
    assert client.ttls == {"rl:bucket:key": 120}
    await store.aclose()
    assert client.closed is True

    # 空 namespace:不拼前缀(调用方可能已自带 namespace)
    bare = RedisRateLimitStore(FakeRedis(), namespace="")
    assert await bare.increment("rl:2:ip", ttl_seconds=120) == 1


def test_build_rate_limiter_without_redis_url_uses_memory() -> None:
    limiter = build_rate_limiter(
        limit=10, redis_url=None, namespace="api-gateway"
    )
    assert isinstance(limiter, SlidingWindowLimiter)


async def test_build_rate_limiter_with_redis_url_uses_redis(monkeypatch) -> None:
    client = FakeRedis()
    calls: list[tuple[str, dict]] = []

    def fake_from_url(url: str, **kwargs: object) -> FakeRedis:
        calls.append((url, dict(kwargs)))
        return client

    monkeypatch.setattr(rate_limit, "from_url", fake_from_url)
    limiter = build_rate_limiter(
        limit=2,
        redis_url="redis://cache:6379/0",
        namespace="api-gateway",
    )
    assert isinstance(limiter, RedisFixedWindowLimiter)
    assert calls == [("redis://cache:6379/0", {"decode_responses": True})]

    assert await limiter.allow("ip:group") is True
    # namespace 由 limiter 拼进桶键;桶号由真实时钟决定,只断言前缀/后缀。
    keys = list(client.data)
    assert len(keys) == 1
    assert keys[0].startswith("api-gateway:")
    assert keys[0].endswith(":ip:group")

    await limiter.aclose()
    assert client.closed is True


@pytest.mark.parametrize("limit", [0, 1])
async def test_zero_and_one_limits(limit: int) -> None:
    limiter = _limiter(limit=limit)
    assert await limiter.allow("k") is (limit == 1)
    assert await limiter.allow("k") is False
