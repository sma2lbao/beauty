"""BYOK 解析与按租户注入:account-service 全走 httpx.MockTransport,零网络。

覆盖:未启用/无租户直接回退、成功解析与请求头、正/负缓存与 TTL、
错误不缓存、缓存上限 LRU 淘汰、并发只穿透一次、响应坏形状、
审计日志不含密钥,以及 API 层"BYOK 覆盖平台 key / 无命中回退平台 key"。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import litellm
import pytest
from common.metrics import metrics_payload, service_label
from common.service_testkit import build_settings
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.metrics import llm_byok_lookups_total
from llm_gateway.services.byok import ByokResolver, ResolvedKey

KEY = "sk-tenant-secret"
HINT = "sk-…-tail"
SECRET = "internal-secret"
HEADERS = {"X-Internal-Secret": SECRET, "X-Tenant-Id": "t1"}

BODY = {
    "model": "deepseek/deepseek-chat",
    "messages": [{"role": "user", "content": "你好"}],
}


def _settings(**overrides) -> Settings:
    values = {
        "byok_enabled": True,
        "internal_secret": SECRET,
        "account_base_url": "http://account.test",
    }
    values.update(overrides)
    return build_settings(Settings, **values)


def _key_payload(tenant: str = "t1") -> dict:
    return {"api_key": f"{KEY}-{tenant}", "key_hint": HINT, "key_id": "key_1"}


class AccountStub:
    """account-service 内部端点桩:记录每次请求,按回调回响应。"""

    def __init__(self, respond=None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or (
            lambda request: httpx.Response(200, json=_key_payload())
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)


def _resolver(stub: AccountStub, **overrides) -> ByokResolver:
    return ByokResolver(_settings(**overrides), transport=httpx.MockTransport(stub))


def _resolve(
    resolver: ByokResolver,
    tenant: str | None = "t1",
    provider: str = "deepseek",
    *,
    count: int = 1,
) -> list[ResolvedKey | None]:
    """在独立事件循环里连续解析 count 次(模拟多个请求),结束时关连接池。"""

    async def scenario() -> list[ResolvedKey | None]:
        try:
            return [await resolver.resolve(tenant, provider) for _ in range(count)]
        finally:
            await resolver.aclose()

    return asyncio.run(scenario())


def _status_count(status: str) -> float:
    return llm_byok_lookups_total.labels(
        service=service_label(), status=status
    )._value.get()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("overrides", "tenant"),
    [
        ({"internal_secret": ""}, "t1"),  # 未配共享密钥 = BYOK 停用
        ({"byok_enabled": False}, "t1"),  # 显式关闭
        ({}, None),  # 无租户(不是 BYOK 调用)
        ({}, ""),  # 空租户
    ],
)
def test_disabled_paths_skip_account(overrides, tenant) -> None:
    stub = AccountStub()
    before = _status_count("disabled")
    assert _resolve(_resolver(stub, **overrides), tenant=tenant) == [None]
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
    assert request.headers["X-Tenant-Id"] == "t1"


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
            200, json={"api_key": f"key-{request.headers['X-Tenant-Id']}"}
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

    async def fake_fetch(tenant_id: str, provider: str):
        calls.append((tenant_id, provider))
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
    assert entry["tenant"] == "t1"
    assert entry["provider"] == "deepseek"
    assert entry["key_hint"] == HINT
    assert entry["source"] == "byok"
    assert KEY not in json.dumps(entry, default=str, ensure_ascii=False)


def test_byok_metric_is_registered() -> None:
    assert b"# TYPE llm_byok_lookups_total counter" in metrics_payload()


# ── API 层:按租户注入 key / 回退平台 key ─────────────────────────────────────────


def _fake_completion(captured: dict):
    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        message = type("M", (), {"content": "ok", "tool_calls": None})()
        return type(
            "R",
            (),
            {
                "choices": [type("C", (), {"message": message})()],
                "usage": None,
                "model": kwargs.get("model"),
            },
        )()

    return fake_acompletion


def _client_with_stub(stub: AccountStub, **overrides) -> TestClient:
    settings = _settings(api_key="sk-platform", **overrides)
    resolver = ByokResolver(settings, transport=httpx.MockTransport(stub))
    return TestClient(create_app(settings, byok_resolver=resolver))


def test_api_completion_uses_byok_key(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _fake_completion(captured))

    with _client_with_stub(stub) as client:
        resp = client.post("/v1/completions", json=BODY, headers=HEADERS)

    assert resp.status_code == 200
    assert captured["api_key"] == f"{KEY}-t1"  # BYOK 覆盖平台 key
    assert "api_base" not in captured  # 平台没配 api_base 时不传
    assert stub.requests[0].headers["X-Tenant-Id"] == "t1"


def test_api_completion_without_tenant_uses_platform_key(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _fake_completion(captured))

    with _client_with_stub(stub) as client:
        resp = client.post(
            "/v1/completions", json=BODY, headers={"X-Internal-Secret": SECRET}
        )

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-platform"
    assert stub.requests == []  # 无租户头 = 不查 account


def test_api_completion_missing_byok_falls_back(monkeypatch) -> None:
    stub = AccountStub(lambda request: httpx.Response(404))
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _fake_completion(captured))

    with _client_with_stub(stub) as client:
        resp = client.post("/v1/completions", json=BODY, headers=HEADERS)

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-platform"


def test_api_completion_without_provider_prefix_skips_byok(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _fake_completion(captured))

    with _client_with_stub(stub) as client:
        resp = client.post(
            "/v1/completions",
            json={**BODY, "model": "gpt-4o-mini"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-platform"
    assert stub.requests == []  # 模型串没有 provider 前缀,不猜


def test_api_stream_uses_byok_key(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        async def chunks():
            delta = type("D", (), {"content": "你", "tool_calls": None})()
            yield type(
                "Chunk",
                (),
                {
                    "choices": [type("C", (), {"delta": delta})()],
                    "model": "deepseek/deepseek-chat",
                },
            )()

        return chunks()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client_with_stub(stub) as client:
        resp = client.post("/v1/completions/stream", json=BODY, headers=HEADERS)

    assert resp.status_code == 200
    assert captured["api_key"] == f"{KEY}-t1"
    assert '"done":true' in resp.text  # 收尾帧已产出(SSE 已开始流式返回)


def test_api_embeddings_uses_byok_key(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}

    async def fake_aembedding(**kwargs):
        captured.update(kwargs)
        return {
            "data": [{"embedding": [0.1, 0.2]}],
            "model": "openai/text-embedding-3-small",
            "usage": None,
        }

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    with _client_with_stub(stub) as client:
        resp = client.post(
            "/v1/embeddings",
            json={"model": "openai/text-embedding-3-small", "input": ["hi"]},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    assert captured["api_key"] == f"{KEY}-t1"
    assert captured["provider"] == "openai"  # 显式 provider 供 litellm 路由
    assert stub.requests[0].url.path == "/v1/byok/openai/secret"


def test_api_embeddings_without_provider_prefix_uses_platform(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}

    async def fake_aembedding(**kwargs):
        captured.update(kwargs)
        return {"data": [{"embedding": [0.1]}], "model": "bge-m3", "usage": None}

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    with _client_with_stub(stub) as client:
        resp = client.post("/v1/embeddings", json={"input": ["hi"]}, headers=HEADERS)

    assert resp.status_code == 200
    assert "api_key" not in captured  # 默认嵌入模型无前缀:不查 BYOK、不传平台 key
    assert stub.requests == []
