"""BYOK 解析:未启用/无账号回退、缓存、并发、审计与指标(零网络)。

覆盖:未启用/无账号直接回退、成功解析与请求头、正/负缓存与 TTL、
错误不缓存、缓存上限 LRU 淘汰、并发只穿透一次、响应坏形状、
审计日志不含密钥、指标注册。API 层注入见 test_byok_api.py,
账号自定义模型见 test_byok_custom_models.py。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from common.metrics import metrics_payload
from structlog.testing import capture_logs

from llm_gateway.services.byok import ResolvedKey
from tests.byok_support import (
    HINT,
    KEY,
    SECRET,
    AccountStub,
    _resolve,
    _resolver,
    _status_count,
)


@pytest.mark.parametrize(
    ("overrides", "account"),
    [
        ({"internal_secret": ""}, "t1"),  # 未配共享密钥 = BYOK 停用
        ({"byok_enabled": False}, "t1"),  # 显式关闭
        ({}, None),  # 无账号(不是 BYOK 调用)
        ({}, ""),  # 空账号
    ],
)
def test_disabled_paths_skip_account(overrides, account) -> None:
    stub = AccountStub()
    before = _status_count("disabled")
    assert _resolve(_resolver(stub, **overrides), account=account) == [None]
    assert stub.requests == []  # 完全没发请求
    assert _status_count("disabled") - before == 1


def test_resolve_returns_key_and_sends_internal_headers() -> None:
    stub = AccountStub()
    before = _status_count("byok")
    [key] = _resolve(_resolver(stub))

    assert isinstance(key, ResolvedKey)
    assert key.api_key == f"{KEY}-t1"
    assert key.source == "byok"
    assert key.provider == "deepseek"
    assert key.key_hint == HINT
    assert key.key_id == "key_1"
    assert KEY not in repr(key)  # 密钥不进 repr(防误打日志)
    assert _status_count("byok") - before == 1

    request = stub.requests[0]
    assert request.method == "GET"
    assert request.url.host == "account.test"
    assert request.url.path == "/v1/byok/deepseek/secret"
    assert request.headers["X-Internal-Secret"] == SECRET
    assert request.headers["X-Account-Id"] == "t1"


def test_cache_hit_does_not_hit_account_again() -> None:
    stub = AccountStub()
    before_hit = _status_count("cache_hit")
    first, second = _resolve(_resolver(stub), count=2)

    assert len(stub.requests) == 1  # 第二次命中缓存
    assert first is not None and second is not None
    assert first.api_key == second.api_key == f"{KEY}-t1"
    assert _status_count("cache_hit") - before_hit == 1


def test_negative_cache_remembers_404_shortly() -> None:
    stub = AccountStub(lambda request: httpx.Response(404))
    before = _status_count("negative")
    assert _resolve(_resolver(stub), count=2) == [None, None]

    assert len(stub.requests) == 1  # 404 也缓存(负缓存)
    assert _status_count("negative") - before == 1


def test_negative_cache_ttl_expiry_refetches() -> None:
    stub = AccountStub(lambda request: httpx.Response(404))
    assert _resolve(_resolver(stub, byok_negative_cache_ttl_seconds=0), count=2) == [
        None,
        None,
    ]
    assert len(stub.requests) == 2


def test_positive_cache_ttl_expiry_refetches() -> None:
    stub = AccountStub()
    keys = _resolve(_resolver(stub, byok_cache_ttl_seconds=0), count=2)

    assert len(stub.requests) == 2
    assert all(key is not None for key in keys)


def test_upstream_error_is_not_cached() -> None:
    stub = AccountStub(lambda request: httpx.Response(500, text="boom"))
    before = _status_count("error")
    assert _resolve(_resolver(stub), count=2) == [None, None]

    assert len(stub.requests) == 2  # 故障不缓存,下一请求立即重试
    assert _status_count("error") - before == 2


def test_network_error_falls_back_to_platform_key() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    assert _resolve(_resolver(AccountStub(boom))) == [None]


def test_missing_api_key_in_payload_is_error() -> None:
    stub = AccountStub(lambda request: httpx.Response(200, json={"key_hint": "x"}))
    assert _resolve(_resolver(stub)) == [None]
    assert len(stub.requests) == 1


def test_bad_json_is_error() -> None:
    stub = AccountStub(lambda request: httpx.Response(200, content=b"<html>"))
    assert _resolve(_resolver(stub)) == [None]


def test_cache_max_entries_evicts_least_recently_used() -> None:
    stub = AccountStub(
        lambda request: httpx.Response(
            200, json={"api_key": f"key-{request.headers['X-Account-Id']}"}
        )
    )
    resolver = _resolver(stub, byok_cache_max_entries=1)

    async def scenario() -> list[ResolvedKey | None]:
        try:
            first = await resolver.resolve("t1", "deepseek")
            second = await resolver.resolve("t2", "deepseek")
            first_again = await resolver.resolve("t1", "deepseek")
            return [first, second, first_again]
        finally:
            await resolver.aclose()

    first, second, first_again = asyncio.run(scenario())
    assert [first.api_key, second.api_key] == ["key-t1", "key-t2"]
    assert first_again.api_key == "key-t1"  # t1 已被挤掉,重新解析
    assert len(stub.requests) == 3


def test_concurrent_resolve_penetrates_account_once() -> None:
    calls: list[tuple[str, str]] = []
    resolver = _resolver(AccountStub())

    async def fake_fetch(account_id: str, provider: str):
        calls.append((account_id, provider))
        await asyncio.sleep(0)  # 让出事件循环,制造并发窗口
        return ResolvedKey(api_key=KEY, provider=provider), "byok"

    resolver._fetch = fake_fetch  # type: ignore[method-assign]

    async def scenario() -> list[ResolvedKey | None]:
        try:
            return await asyncio.gather(
                *(resolver.resolve("t1", "deepseek") for _ in range(5))
            )
        finally:
            await resolver.aclose()

    keys = asyncio.run(scenario())
    assert len(calls) == 1  # 其余 4 个在锁上等到缓存后命中
    assert all(key is not None and key.api_key == KEY for key in keys)


def test_audit_log_omits_api_key() -> None:
    stub = AccountStub()
    with capture_logs() as logs:
        [key] = _resolve(_resolver(stub))

    assert key is not None
    events = [entry for entry in logs if entry.get("event") == "byok_resolved"]
    assert len(events) == 1
    entry = events[0]
    assert entry["account"] == "t1"
    assert entry["provider"] == "deepseek"
    assert entry["key_hint"] == HINT
    assert entry["source"] == "byok"
    assert KEY not in json.dumps(entry, default=str, ensure_ascii=False)


def test_byok_metric_is_registered() -> None:
    assert b"# TYPE llm_byok_lookups_total counter" in metrics_payload()
