"""限流中间件:进程内回退、Redis 装配选择、注入 limiter 拒绝。"""

from __future__ import annotations

import httpx
import pytest
from common.security import (
    RedisFixedWindowLimiter,
    SlidingWindowLimiter,
    rate_limit,
)
from fastapi.testclient import TestClient

from api_gateway.main import create_app
from tests.conftest import handler, login, settings


class DenyLimiter:
    """假限流器:永远拒绝,并记录调用与关闭。"""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.closed = False

    async def allow(self, key: str) -> bool:
        self.keys.append(key)
        return False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("client", [{"rate_limit_per_minute": 2}], indirect=True)
def test_rate_limit_returns_429(client: TestClient) -> None:
    login(client)
    assert client.get("/api/v1/runs").status_code == 200
    assert client.get("/api/v1/runs").status_code == 200
    limited = client.get("/api/v1/runs")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"


def test_injected_limiter_rejection_returns_429() -> None:
    limiter = DenyLimiter()
    app = create_app(
        settings(),
        transport=httpx.MockTransport(handler),
        rate_limiter=limiter,
    )
    with TestClient(app) as client:
        limited = client.get("/api/v1/runs")
        assert limited.status_code == 429
        assert limited.json()["code"] == "api_gateway.rate_limited"
    assert limiter.keys == ["testclient:/api/v1/runs"]
    assert limiter.closed is True


def test_default_backend_is_in_process() -> None:
    app = create_app(settings(), transport=httpx.MockTransport(handler))
    assert isinstance(app.state.rate_limiter, SlidingWindowLimiter)


def test_redis_url_selects_redis_backend(monkeypatch) -> None:
    created: dict[str, object] = {}

    class FakeRedis:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    fake_client = FakeRedis()

    def fake_from_url(url: str, **kwargs: object) -> FakeRedis:
        created["url"] = url
        created["kwargs"] = dict(kwargs)
        return fake_client

    monkeypatch.setattr(rate_limit, "from_url", fake_from_url)
    app = create_app(
        settings(redis_url="redis://redis:6379/0"),
        transport=httpx.MockTransport(handler),
    )
    assert isinstance(app.state.rate_limiter, RedisFixedWindowLimiter)
    assert created == {
        "url": "redis://redis:6379/0",
        "kwargs": {"decode_responses": True},
    }
    with TestClient(app):
        pass
    # lifespan 关闭时回收 Redis 连接池
    assert fake_client.closed is True
