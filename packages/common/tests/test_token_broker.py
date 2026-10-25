"""common.security.token_broker:换发/缓存/错误映射(httpx.MockTransport,零网络)。"""

from __future__ import annotations

import json

import httpx
import pytest

from common.errors import UpstreamError
from common.security.token_broker import (
    DEFAULT_SUBJECT,
    REFRESH_MARGIN_SECONDS,
    TokenBroker,
)

ISSUE_BODY = {"access_token": "tok-1", "expires_in": 3600, "tenant_id": "t-a"}


def _broker(
    handler,
    *,
    secret: str = "s3cret",
    subject: str = DEFAULT_SUBJECT,
) -> TokenBroker:
    return TokenBroker(
        base_url="http://account.local",
        internal_secret=secret,
        subject=subject,
        transport=httpx.MockTransport(handler),
    )


async def test_token_issued_with_secret_header() -> None:
    """换发:带内部密钥头与目标租户,返回 token。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ISSUE_BODY)

    broker = _broker(handler)
    assert await broker.token("t-a") == "tok-1"
    assert seen[0].headers["X-Internal-Secret"] == "s3cret"
    assert json.loads(seen[0].read()) == {
        "tenant_id": "t-a",
        "subject": "svc-orchestrator",
    }
    await broker.aclose()


async def test_subject_is_configurable() -> None:
    """subject 标识调用方服务身份:tool-service 用 svc-tool。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ISSUE_BODY)

    broker = _broker(handler, subject="svc-tool")
    assert await broker.token("t-a") == "tok-1"
    assert json.loads(seen[0].read())["subject"] == "svc-tool"
    await broker.aclose()


async def test_token_cached_until_expiry() -> None:
    """缓存:未过期前同租户复用,零重复请求;不同租户各自换发。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=ISSUE_BODY)

    broker = _broker(handler)
    assert await broker.token("t-a") == "tok-1"
    assert await broker.token("t-a") == "tok-1"
    assert await broker.token("t-b") == "tok-1"
    assert calls["n"] == 2
    await broker.aclose()


async def test_short_lived_token_is_refreshed() -> None:
    """剩余寿命不足刷新余量时不缓存(expires_in 小于余量 → 每次都换发)。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200, json={"access_token": f"tok-{calls['n']}", "expires_in": 1}
        )

    broker = _broker(handler)
    assert await broker.token("t-a") == "tok-1"
    assert await broker.token("t-a") == "tok-2"
    assert calls["n"] == 2
    assert REFRESH_MARGIN_SECONDS == 60
    await broker.aclose()


async def test_broker_maps_upstream_error() -> None:
    """签发端点拒绝(401)→ UpstreamError,details 带截断响应体。"""
    broker = _broker(lambda _: httpx.Response(401, text="nope" * 100))
    with pytest.raises(UpstreamError) as excinfo:
        await broker.token("t-a")
    assert excinfo.value.code == "auth.token_broker_error"
    assert len(excinfo.value.details["body"]) <= 200
    await broker.aclose()


async def test_broker_maps_unreachable() -> None:
    """传输层失败 → auth.token_broker_unavailable,不裸抛 httpx 异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("拒绝连接", request=request)

    broker = _broker(handler)
    with pytest.raises(UpstreamError) as excinfo:
        await broker.token("t-a")
    assert excinfo.value.code == "auth.token_broker_unavailable"
    await broker.aclose()


async def test_broker_requires_internal_secret() -> None:
    """未配置内部密钥:换发处即报错,不做无鉴权裸调。"""
    broker = _broker(lambda _: httpx.Response(500), secret="")
    with pytest.raises(UpstreamError) as excinfo:
        await broker.token("t-a")
    assert excinfo.value.code == "auth.internal_secret_missing"
    await broker.aclose()
