"""BYOK 测试共用装配:account-service 桩、Settings 与解析助手(零网络)。

只给测试用(不在 llm_gateway 包内,不进覆盖率分母)。
"""

from __future__ import annotations

import asyncio

import httpx
from common.metrics import service_label
from common.service_testkit import build_settings
from fastapi.testclient import TestClient

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.metrics import llm_byok_lookups_total
from llm_gateway.services.byok import ByokResolver, ResolvedKey
from llm_gateway.services.byok_catalog import ByokCatalog

KEY = "sk-account-secret"
HINT = "sk-…-tail"
SECRET = "internal-secret"
HEADERS = {"X-Internal-Secret": SECRET, "X-Account-Id": "t1"}

BODY = {
    "model": "deepseek/deepseek-chat",
    "messages": [{"role": "user", "content": "你好"}],
}


def _settings(**overrides) -> Settings:
    values = {
        "byok_enabled": True,
        "internal_secret": SECRET,
        "account_base_url": "http://account.test",
        # 钉死模型配置:Nx 会注入项目 .env,避免白名单/fl备用链影响本文件语义
        "available_models": [],
        "fallback_models": [],
    }
    values.update(overrides)
    return build_settings(Settings, **values)


def _key_payload(account: str = "t1") -> dict:
    return {"api_key": f"{KEY}-{account}", "key_hint": HINT, "key_id": "key_1"}


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
    account: str | None = "t1",
    provider: str = "deepseek",
    *,
    count: int = 1,
) -> list[ResolvedKey | None]:
    """在独立事件循环里连续解析 count 次(模拟多个请求),结束时关连接池。"""

    async def scenario() -> list[ResolvedKey | None]:
        try:
            return [await resolver.resolve(account, provider) for _ in range(count)]
        finally:
            await resolver.aclose()

    return asyncio.run(scenario())


def _status_count(status: str) -> float:
    return llm_byok_lookups_total.labels(
        service=service_label(), status=status
    )._value.get()  # type: ignore[attr-defined]


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
    # 目录端点也打桩(404 -> 空目录):请求带账号头时不会真连 account-service
    catalog = ByokCatalog(
        settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    return TestClient(
        create_app(settings, byok_resolver=resolver, byok_catalog=catalog)
    )
