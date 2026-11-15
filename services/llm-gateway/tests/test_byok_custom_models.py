"""账号自定义模型 + BYOK key:completions 与 stream 都放行。

目录端点与密钥端点共用同一个账户桩,全部走 httpx.MockTransport:零网络。
"""

from __future__ import annotations

import httpx
import litellm
from fastapi.testclient import TestClient

from llm_gateway.main import create_app
from llm_gateway.services.byok import ByokResolver
from llm_gateway.services.byok_catalog import ByokCatalog
from tests.byok_support import (
    BODY,
    HEADERS,
    KEY,
    _fake_completion,
    _key_payload,
    _settings,
)

CATALOG_MODEL = "openai/gpt-4o-mini"
PLATFORM_MODEL = "deepseek/deepseek-chat"


class CatalogAccountStub:
    """同时服务目录与密钥端点:记录请求,按路径分发响应。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/byok/catalog":
            return httpx.Response(200, json={"models": [CATALOG_MODEL]})
        return httpx.Response(200, json=_key_payload())


def _client_with_catalog(stub: CatalogAccountStub, **overrides) -> TestClient:
    settings = _settings(
        api_key="sk-platform", available_models=[PLATFORM_MODEL], **overrides
    )
    resolver = ByokResolver(settings, transport=httpx.MockTransport(stub))
    catalog = ByokCatalog(settings, transport=httpx.MockTransport(stub))
    return TestClient(
        create_app(settings, byok_resolver=resolver, byok_catalog=catalog)
    )


def test_api_completion_custom_model_uses_byok_key(monkeypatch) -> None:
    stub = CatalogAccountStub()
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _fake_completion(captured))

    with _client_with_catalog(stub) as client:
        resp = client.post(
            "/v1/completions",
            json={**BODY, "model": CATALOG_MODEL},
            headers=HEADERS,
        )

    assert resp.status_code == 200  # 白名单外但属于账号自定义模型:放行
    assert captured["model"] == CATALOG_MODEL
    assert captured["api_key"] == f"{KEY}-t1"  # 用该模型的 provider 解析 BYOK key
    paths = [request.url.path for request in stub.requests]
    assert paths == ["/v1/byok/catalog", "/v1/byok/openai/secret"]


def test_api_stream_custom_model_uses_byok_key(monkeypatch) -> None:
    stub = CatalogAccountStub()
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
                    "model": CATALOG_MODEL,
                },
            )()

        return chunks()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client_with_catalog(stub) as client:
        resp = client.post(
            "/v1/completions/stream",
            json={**BODY, "model": CATALOG_MODEL},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    assert captured["model"] == CATALOG_MODEL
    assert captured["api_key"] == f"{KEY}-t1"
    assert '"done":true' in resp.text


def test_api_custom_model_without_catalog_stays_400(monkeypatch) -> None:
    """目录故障时白名单仍按平台列表拦截,不会因为上次放行而漏过。"""

    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    settings = _settings(api_key="sk-platform", available_models=[PLATFORM_MODEL])
    catalog = ByokCatalog(settings, transport=httpx.MockTransport(boom))
    called: list[str] = []

    async def fake_acompletion(**kwargs):
        called.append(kwargs["model"])
        return None

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with TestClient(create_app(settings, byok_catalog=catalog)) as client:
        resp = client.post(
            "/v1/completions",
            json={**BODY, "model": CATALOG_MODEL},
            headers=HEADERS,
        )

    assert resp.status_code == 400
    assert resp.json()["code"] == "llm.model_not_allowed"
    assert called == []
