"""账号 BYOK 自定义模型目录:account-service 全走 httpx.MockTransport,零网络。

覆盖:未启用/无账号直接空列表且不请求、成功解析与内部请求头、按账号
TTL 缓存与命中/过期、非 200/网络错误/坏 JSON 不缓存且降级为空、脏条目
过滤、指标 status、以及日志不含内部密钥。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from common.metrics import metrics_payload, service_label
from common.service_testkit import build_settings
from structlog.testing import capture_logs

from llm_gateway.core.config import Settings
from llm_gateway.metrics import llm_byok_catalog_lookups_total
from llm_gateway.services.byok_catalog import ByokCatalog

SECRET = "internal-secret"
MODELS = ["openai/gpt-4o-mini", "deepseek/deepseek-chat"]


def _settings(**overrides) -> Settings:
    values = {
        "byok_enabled": True,
        "internal_secret": SECRET,
        "account_base_url": "http://account.test",
        # 钉死模型配置:Nx 会注入项目 .env,避免白名单/fallback 影响本文件语义
        "available_models": [],
        "fallback_models": [],
    }
    values.update(overrides)
    return build_settings(Settings, **values)


class CatalogStub:
    """account-service 模型目录端点桩:记录每次请求,按回调回响应。"""

    def __init__(self, respond=None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or (
            lambda request: httpx.Response(200, json=_catalog())
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)


def _catalog(models: list | None = None) -> dict:
    return {"models": MODELS if models is None else models}


def _service(**overrides) -> ByokCatalog:
    return ByokCatalog(
        _settings(**overrides), transport=httpx.MockTransport(CatalogStub())
    )


def _catalog_with(stub: CatalogStub, **overrides) -> ByokCatalog:
    return ByokCatalog(_settings(**overrides), transport=httpx.MockTransport(stub))


def _custom_models(
    catalog: ByokCatalog,
    account: str | None = "t1",
    *,
    count: int = 1,
) -> list[list[str]]:
    """在独立事件循环里连续查询 count 次(模拟多个请求),结束时关连接池。"""

    async def scenario() -> list[list[str]]:
        try:
            return [await catalog.custom_models(account) for _ in range(count)]
        finally:
            await catalog.aclose()

    return asyncio.run(scenario())


def _status_count(status: str) -> float:
    return llm_byok_catalog_lookups_total.labels(
        service=service_label(), status=status
    )._value.get()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("overrides", "account"),
    [
        ({"internal_secret": ""}, "t1"),  # 未配共享密钥 = 内部端点不可用,停用
        ({"byok_enabled": False}, "t1"),  # 显式关闭
        ({}, None),  # 无账号(不是 BYOK 调用)
        ({}, ""),  # 空账号
    ],
)
def test_disabled_paths_skip_account(overrides, account) -> None:
    stub = CatalogStub()
    before = _status_count("disabled")
    assert _custom_models(_catalog_with(stub, **overrides), account=account) == [[]]
    assert stub.requests == []  # 完全没发请求
    assert _status_count("disabled") - before == 1


def test_returns_models_and_sends_internal_headers() -> None:
    stub = CatalogStub()
    before = _status_count("miss")
    [models] = _custom_models(_catalog_with(stub))

    assert models == MODELS
    assert _status_count("miss") - before == 1

    request = stub.requests[0]
    assert request.method == "GET"
    assert request.url.host == "account.test"
    assert request.url.path == "/v1/byok/catalog"
    assert request.headers["X-Internal-Secret"] == SECRET
    assert request.headers["X-Account-Id"] == "t1"


def test_cache_hit_does_not_hit_account_again() -> None:
    stub = CatalogStub()
    before = _status_count("hit")
    first, second = _custom_models(_catalog_with(stub), count=2)

    assert len(stub.requests) == 1  # 第二次命中缓存
    assert first == second == MODELS
    assert _status_count("hit") - before == 1


def test_cache_returns_defensive_copy() -> None:
    stub = CatalogStub()

    async def scenario() -> list[str]:
        catalog = _catalog_with(stub)
        try:
            first = await catalog.custom_models("t1")
            first.append("polluted/model")
            return await catalog.custom_models("t1")
        finally:
            await catalog.aclose()

    assert asyncio.run(scenario()) == MODELS
    assert len(stub.requests) == 1


def test_cache_ttl_expiry_refetches() -> None:
    stub = CatalogStub()
    _custom_models(_catalog_with(stub, byok_catalog_cache_ttl_seconds=0), count=2)
    assert len(stub.requests) == 2


def test_cache_is_per_account() -> None:
    stub = CatalogStub(
        lambda request: httpx.Response(
            200,
            json={"models": [f"{request.headers['X-Account-Id']}/model"]},
        )
    )

    async def scenario() -> list[list[str]]:
        catalog = _catalog_with(stub)
        try:
            return [
                await catalog.custom_models("t1"),
                await catalog.custom_models("t2"),
                await catalog.custom_models("t1"),
            ]
        finally:
            await catalog.aclose()

    assert asyncio.run(scenario()) == [["t1/model"], ["t2/model"], ["t1/model"]]
    assert len(stub.requests) == 2  # t1 第二次命中,不会串到 t2 的缓存


def test_upstream_error_is_not_cached() -> None:
    stub = CatalogStub(lambda request: httpx.Response(500, text="boom"))
    before = _status_count("error")
    with capture_logs() as logs:
        assert _custom_models(_catalog_with(stub), count=2) == [[], []]

    assert len(stub.requests) == 2  # 故障不缓存,下一请求立即重试
    assert _status_count("error") - before == 2
    events = [
        entry for entry in logs if entry.get("event") == "byok_catalog_lookup_failed"
    ]
    assert len(events) == 2
    assert events[0]["account"] == "t1"
    assert events[0]["status"] == 500
    assert SECRET not in json.dumps(events, default=str, ensure_ascii=False)


def test_network_error_degrades_to_empty() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with capture_logs() as logs:
        assert _custom_models(_catalog_with(CatalogStub(boom))) == [[]]

    events = [
        entry for entry in logs if entry.get("event") == "byok_catalog_lookup_failed"
    ]
    assert events[0]["reason"] == "ConnectError"
    assert events[0]["account"] == "t1"


def test_bad_json_is_error_and_not_cached() -> None:
    stub = CatalogStub(lambda request: httpx.Response(200, content=b"<html>"))
    before = _status_count("error")
    with capture_logs() as logs:
        assert _custom_models(_catalog_with(stub), count=2) == [[], []]

    assert len(stub.requests) == 2
    assert _status_count("error") - before == 2
    events = [
        entry for entry in logs if entry.get("event") == "byok_catalog_lookup_failed"
    ]
    assert events[0]["reason"] == "bad_json"


def test_filters_non_string_and_empty_entries() -> None:
    stub = CatalogStub(
        lambda request: httpx.Response(
            200, json=_catalog(["openai/a", None, 3, "", "deepseek/b"])
        )
    )
    assert _custom_models(_catalog_with(stub)) == [["openai/a", "deepseek/b"]]


@pytest.mark.parametrize(
    "payload",
    [
        {"models": "openai/a"},  # models 不是 list
        {"models": None},
        {"foo": "bar"},  # 缺 models
        ["openai/a"],  # 顶层不是对象
    ],
)
def test_bad_payload_shape_degrades_to_empty(payload) -> None:
    stub = CatalogStub(lambda request: httpx.Response(200, json=payload))
    before = _status_count("miss")
    # 形状不对是可解析响应:降级为空列表但仍算成功查询(可缓存)
    assert _custom_models(_catalog_with(stub), count=2) == [[], []]

    assert len(stub.requests) == 1  # 空列表也缓存,避免每次打 account
    assert _status_count("miss") - before == 1


def test_aclose_releases_client_and_is_idempotent() -> None:
    catalog = _catalog_with(CatalogStub())

    async def scenario() -> None:
        assert await catalog.custom_models("t1") == MODELS
        assert catalog._client is not None  # noqa: SLF001
        await catalog.aclose()
        assert catalog._client is None  # noqa: SLF001
        await catalog.aclose()  # 重复关闭 no-op

    asyncio.run(scenario())


def test_metric_is_registered() -> None:
    assert b"# TYPE llm_byok_catalog_lookups_total counter" in metrics_payload()
