"""模型路由与白名单(model-routing §3):显式 > 默认;白名单外一律 400。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import litellm
import pytest
from common.errors import AppError
from common.service_testkit import app_client, build_settings
from fastapi.testclient import TestClient

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.services import completions
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
