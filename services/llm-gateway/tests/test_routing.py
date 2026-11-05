"""模型路由与白名单(model-routing §3):显式 > 默认;白名单外一律 400。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import litellm
import pytest
from common.errors import AppError
from common.service_testkit import app_client, build_settings
from fastapi.testclient import TestClient

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.services import completions
from llm_gateway.services.byok import ByokResolver
from llm_gateway.services.byok_catalog import ByokCatalog
from tests.fakes import chunk, response, sse_frames, stream

ALLOWED = "prov-a/allowed"
OTHER = "prov-b/other"
BODY = {"messages": [{"role": "user", "content": "你好"}]}


@contextmanager
def _client(**overrides: Any) -> Iterator[TestClient]:
    settings = build_settings(Settings, **overrides)
    with app_client(create_app(settings)) as client:
        yield client


def _stub(monkeypatch, captured: dict | None = None):
    async def fake_acompletion(**kwargs: Any) -> Any:
        if captured is not None:
            captured.update(kwargs)
        return response(model=kwargs["model"])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)


def test_allowed_model_passes(client: TestClient, monkeypatch) -> None:
    captured: dict = {}
    _stub(monkeypatch, captured)
    resp = client.post("/v1/completions", json={**BODY, "model": ALLOWED})

    assert resp.status_code == 200
    assert captured["model"] == ALLOWED


def test_model_not_in_whitelist_is_400(monkeypatch) -> None:
    called: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        called.append(kwargs["model"])
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client(available_models=[ALLOWED], fallback_models=[]) as client:
        resp = client.post("/v1/completions", json={**BODY, "model": OTHER})

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "llm.model_not_allowed"
    assert body["details"]["model"] == OTHER
    assert called == []  # 白名单校验先于上游调用


def test_stream_model_not_in_whitelist_is_400_json(monkeypatch) -> None:
    settings = build_settings(Settings, available_models=[ALLOWED], fallback_models=[])
    with app_client(create_app(settings)) as client:
        resp = client.post("/v1/completions/stream", json={**BODY, "model": OTHER})

    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["code"] == "llm.model_not_allowed"


def test_empty_whitelist_allows_any_model(client: TestClient, monkeypatch) -> None:
    _stub(monkeypatch)
    resp = client.post("/v1/completions", json={**BODY, "model": OTHER})

    assert resp.status_code == 200


def test_default_model_must_be_whitelisted(monkeypatch) -> None:
    """不传 model 时默认模型也要过白名单,不允许"隐式越权"。"""
    settings = build_settings(
        Settings, available_models=["prov-a/only"], fallback_models=[]
    )
    with app_client(create_app(settings)) as client:
        resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 400
    assert resp.json()["code"] == "llm.model_not_allowed"


def test_default_model_in_whitelist_passes(client: TestClient, monkeypatch) -> None:
    captured: dict = {}
    _stub(monkeypatch, captured)
    resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 200
    assert captured["model"] == "deepseek/deepseek-chat"


def test_resolve_model_and_candidates_helpers() -> None:
    settings = build_settings(
        Settings,
        available_models=[ALLOWED],
        fallback_models=[OTHER, ALLOWED, ""],
    )
    assert completions.resolve_model(settings, ALLOWED) == ALLOWED
    # 默认模型不在白名单时同样 400(不允许隐式越权)
    with pytest.raises(AppError) as excinfo:
        completions.resolve_model(settings, None)
    assert excinfo.value.code == "llm.model_not_allowed"
    # 去重去空、主模型永远在最前
    assert completions.candidates(settings, ALLOWED) == [ALLOWED, OTHER]

    open_settings = build_settings(Settings, available_models=[], fallback_models=[])
    assert completions.resolve_model(open_settings, None) == open_settings.default_model


def test_stream_whitelisted_model_streams(client: TestClient, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", stream([chunk("好")], captured))
    resp = client.post("/v1/completions/stream", json={**BODY, "model": ALLOWED})

    assert resp.status_code == 200
    assert captured["model"] == ALLOWED
    assert sse_frames(resp.text)[-1]["done"] is True


# ── 账号 BYOK 自定义模型:白名单并集放行(目录走 MockTransport) ──────────────────

CUSTOM = "openai/gpt-4o-mini"
SECRET = "internal-secret"
ACCOUNT_HEADERS = {"X-Internal-Secret": SECRET, "X-Account-Id": "t1"}


class CatalogStub:
    """account-service 模型目录桩:记录请求,默认只给 CUSTOM。"""

    def __init__(self, models: list[str] | None = None, respond=None) -> None:
        self.requests: list[httpx.Request] = []
        self._models = [CUSTOM] if models is None else models
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._respond is not None:
            return self._respond(request)
        return httpx.Response(200, json={"models": self._models})


@contextmanager
def _client_with_catalog(stub: CatalogStub, **overrides: Any) -> Iterator[TestClient]:
    values: dict[str, Any] = {
        "byok_enabled": True,
        "internal_secret": SECRET,
        "account_base_url": "http://account.test",
        "available_models": [ALLOWED],
        "fallback_models": [],
    }
    values.update(overrides)
    settings = build_settings(Settings, **values)
    catalog = ByokCatalog(settings, transport=httpx.MockTransport(stub))
    # 密钥解析单独打桩(一律 404 -> 回退平台 key),避免真连 account-service;
    # 这样 stub.requests 里只剩目录请求,断言不受影响。
    resolver = ByokResolver(
        settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    app = create_app(settings, byok_resolver=resolver, byok_catalog=catalog)
    with app_client(app) as client:
        yield client


def test_resolve_model_allows_extra_models_unit() -> None:
    settings = build_settings(Settings, available_models=[ALLOWED], fallback_models=[])

    assert completions.resolve_model(settings, CUSTOM, extra_models=[CUSTOM]) == CUSTOM
    assert (
        completions.resolve_model(settings, CUSTOM, extra_models=iter([CUSTOM]))
        == CUSTOM
    )
    with pytest.raises(AppError) as excinfo:
        completions.resolve_model(settings, CUSTOM)
    assert excinfo.value.code == "llm.model_not_allowed"
    assert excinfo.value.details["model"] == CUSTOM
    assert excinfo.value.details["available_models"] == [ALLOWED]


def test_catalog_custom_model_runs_completion(monkeypatch) -> None:
    stub = CatalogStub()
    captured: dict = {}
    _stub(monkeypatch, captured)

    with _client_with_catalog(stub) as client:
        resp = client.post(
            "/v1/completions", json={**BODY, "model": CUSTOM}, headers=ACCOUNT_HEADERS
        )

    assert resp.status_code == 200
    assert captured["model"] == CUSTOM
    assert [request.url.path for request in stub.requests] == ["/v1/byok/catalog"]


def test_catalog_custom_model_runs_stream(monkeypatch) -> None:
    stub = CatalogStub()
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", stream([chunk("好")], captured))

    with _client_with_catalog(stub) as client:
        resp = client.post(
            "/v1/completions/stream",
            json={**BODY, "model": CUSTOM},
            headers=ACCOUNT_HEADERS,
        )

    assert resp.status_code == 200
    assert captured["model"] == CUSTOM
    assert sse_frames(resp.text)[-1]["done"] is True


def test_custom_model_missing_from_catalog_is_400(monkeypatch) -> None:
    stub = CatalogStub(models=[OTHER])
    called: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        called.append(kwargs["model"])
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client_with_catalog(stub) as client:
        resp = client.post(
            "/v1/completions", json={**BODY, "model": CUSTOM}, headers=ACCOUNT_HEADERS
        )

    assert resp.status_code == 400
    assert resp.json()["code"] == "llm.model_not_allowed"
    assert called == []


def test_stream_unknown_custom_model_is_json_400(monkeypatch) -> None:
    stub = CatalogStub(models=[OTHER])
    with _client_with_catalog(stub) as client:
        resp = client.post(
            "/v1/completions/stream",
            json={**BODY, "model": CUSTOM},
            headers=ACCOUNT_HEADERS,
        )

    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["code"] == "llm.model_not_allowed"


def test_catalog_failure_falls_back_to_platform_whitelist(monkeypatch) -> None:
    stub = CatalogStub(respond=lambda request: httpx.Response(500, text="boom"))
    _stub(monkeypatch)
    with _client_with_catalog(stub) as client:
        resp = client.post(
            "/v1/completions", json={**BODY, "model": CUSTOM}, headers=ACCOUNT_HEADERS
        )

    assert resp.status_code == 400  # 目录故障时仍按平台白名单拦截
    assert resp.json()["code"] == "llm.model_not_allowed"


def test_platform_model_still_allowed_with_catalog(monkeypatch) -> None:
    stub = CatalogStub()
    captured: dict = {}
    _stub(monkeypatch, captured)
    with _client_with_catalog(stub) as client:
        resp = client.post(
            "/v1/completions", json={**BODY, "model": ALLOWED}, headers=ACCOUNT_HEADERS
        )

    assert resp.status_code == 200
    assert captured["model"] == ALLOWED
