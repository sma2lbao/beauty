"""GET /v1/models:平台列表 + 账号 BYOK 自定义模型合并。

合并的前置条件(防枚举):有 X-Account-Id,且配了内部密钥时 X-Internal-Secret
必须匹配;目录故障只降级为平台列表,绝不 500。
"""

from __future__ import annotations

from typing import Any

import httpx
from common.service_testkit import build_settings
from fastapi.testclient import TestClient

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.services.byok_catalog import ByokCatalog

SECRET = "internal-secret"
ACCOUNT = "t1"
ACCOUNT_HEADERS = {"X-Internal-Secret": SECRET, "X-Account-Id": ACCOUNT}
PLATFORM = "deepseek/chat"
CUSTOM = "openai/gpt-4o-mini"


def _settings(**overrides: Any) -> Settings:
    values = {
        "byok_enabled": True,
        "internal_secret": SECRET,
        "account_base_url": "http://account.test",
        "default_model": PLATFORM,
        "available_models": [PLATFORM],
        "fallback_models": [],
    }
    values.update(overrides)
    return build_settings(Settings, **values)


class CatalogStub:
    """账号模型目录端点桩:记录请求,默认返回 CUSTOM。"""

    def __init__(self, respond=None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or (
            lambda request: httpx.Response(200, json={"models": [CUSTOM]})
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)


def _client(stub: CatalogStub, **overrides: Any) -> TestClient:
    settings = _settings(**overrides)
    catalog = ByokCatalog(settings, transport=httpx.MockTransport(stub))
    return TestClient(create_app(settings, byok_catalog=catalog))


def test_models_returns_default_model() -> None:
    with _client(CatalogStub()) as client:
        response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["default_model"]
    ids = [item["id"] for item in body["data"]]
    assert body["default_model"] in ids


def test_models_uses_configured_list() -> None:
    app = create_app(
        Settings(
            env="dev",
            default_model="deepseek/chat",
            available_models=["deepseek/chat", "qwen/plus"],
        )
    )
    with TestClient(app) as test_client:
        body = test_client.get("/v1/models").json()
    assert [item["id"] for item in body["data"]] == ["deepseek/chat", "qwen/plus"]


def test_models_whitelist_rejects_unknown_model() -> None:
    """§3/A 白名单:列表非空时,不在列表内的模型在入口层直接 400。"""
    app = create_app(
        Settings(
            env="dev",
            available_models=["deepseek/chat"],
            fallback_models=[],
            _env_file=None,
        )
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/v1/completions",
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert response.status_code == 400
    assert response.json()["code"] == "llm.model_not_allowed"


# ── 账号自定义模型合并 ─────────────────────────────────────────────────────────


def test_merges_custom_models_for_account() -> None:
    stub = CatalogStub()
    with _client(stub) as client:
        body = client.get("/v1/models", headers=ACCOUNT_HEADERS).json()

    # 平台列表在前,自定义追加在后
    assert [item["id"] for item in body["data"]] == [PLATFORM, CUSTOM]
    owners = {item["id"]: item["owned_by"] for item in body["data"]}
    assert owners == {PLATFORM: "beauty-moon", CUSTOM: "byok"}
    assert body["default_model"] == PLATFORM  # default_model 不变
    assert len(stub.requests) == 1
    assert stub.requests[0].url.path == "/v1/byok/catalog"
    assert stub.requests[0].headers["X-Account-Id"] == ACCOUNT


def test_duplicate_custom_models_are_not_appended_twice() -> None:
    stub = CatalogStub(
        lambda request: httpx.Response(200, json={"models": [PLATFORM, CUSTOM, CUSTOM]})
    )
    with _client(stub) as client:
        body = client.get("/v1/models", headers=ACCOUNT_HEADERS).json()

    assert [item["id"] for item in body["data"]] == [PLATFORM, CUSTOM]


def test_without_account_header_does_not_merge_or_query() -> None:
    stub = CatalogStub()
    with _client(stub) as client:
        body = client.get("/v1/models", headers={"X-Internal-Secret": SECRET}).json()

    assert [item["id"] for item in body["data"]] == [PLATFORM]
    assert stub.requests == []  # 公开调用不查目录(防枚举)


def test_without_internal_secret_does_not_merge_or_query() -> None:
    stub = CatalogStub()
    with _client(stub) as client:
        body = client.get("/v1/models", headers={"X-Account-Id": ACCOUNT}).json()

    assert [item["id"] for item in body["data"]] == [PLATFORM]
    assert stub.requests == []


def test_wrong_internal_secret_does_not_merge_or_query() -> None:
    stub = CatalogStub()
    with _client(stub) as client:
        body = client.get(
            "/v1/models",
            headers={"X-Internal-Secret": "wrong-secret", "X-Account-Id": ACCOUNT},
        ).json()

    assert [item["id"] for item in body["data"]] == [PLATFORM]
    assert stub.requests == []


def test_catalog_error_falls_back_to_platform_list() -> None:
    stub = CatalogStub(lambda request: httpx.Response(503, text="unavailable"))
    with _client(stub) as client:
        response = client.get("/v1/models", headers=ACCOUNT_HEADERS)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [PLATFORM]


def test_catalog_exception_falls_back_to_platform_list(monkeypatch) -> None:
    settings = _settings()
    catalog = ByokCatalog(settings, transport=httpx.MockTransport(CatalogStub()))

    async def boom(account_id: str | None) -> list[str]:
        raise RuntimeError("catalog exploded")

    monkeypatch.setattr(catalog, "custom_models", boom)
    with TestClient(create_app(settings, byok_catalog=catalog)) as client:
        response = client.get("/v1/models", headers=ACCOUNT_HEADERS)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [PLATFORM]


def test_absent_internal_secret_does_not_query_catalog() -> None:
    """未配内部密钥时内部端点不鉴权,但目录客户端自身停用:不发请求、不合并。"""
    stub = CatalogStub()
    with _client(stub, internal_secret="", available_models=[PLATFORM]) as client:
        body = client.get("/v1/models", headers={"X-Account-Id": ACCOUNT}).json()

    # byok_enabled=True 但 internal_secret 为空 => 目录客户端自身停用,不发请求
    assert [item["id"] for item in body["data"]] == [PLATFORM]
    assert stub.requests == []
