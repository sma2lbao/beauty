"""视觉能力(model-routing §7):补写 litellm 能力表 + 带图请求显式拒绝。

背景:litellm 的 DeepSeek 适配层在支持视觉时透传 ``image_url``,否则把
``content`` 列表折叠成纯文本——图片静默消失、模型凭空作答。能力表是随版本
发布的静态数据,新模型(本平台默认的 ``deepseek-chat`` 别名)常常漏标,
所以网关按 ``VISION_MODELS`` 补写,并且对"带图 + 不支持"直接 400。

litellm 全打桩:能力查询与注册都不碰真实网络,也不污染全局能力表。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import litellm
import pytest
from common.errors import AppError
from common.service_testkit import app_client, build_settings
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.schemas.completions import CompletionRequest, Message
from llm_gateway.services import completions, vision
from llm_gateway.services.completion_routing import candidates
from tests.fakes import response

PNG = "data:image/png;base64,iVBORw0KGgo="
CHAT = "deepseek/deepseek-chat"
TEXT_ONLY = "deepseek/deepseek-reasoner"
FLASH = "deepseek/deepseek-flash"
PROBE = "deepseek/vision-probe"


def _content(text: str = "这张图里有什么?") -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": PNG}},
    ]


def _body(model: str, content: Any) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": content}]}


@contextmanager
def _client(**overrides: Any) -> Iterator[TestClient]:
    base: dict[str, Any] = {
        "available_models": [],
        "fallback_models": [],
        "vision_models": [CHAT],
    }
    with app_client(create_app(build_settings(Settings, **{**base, **overrides}))) as c:
        yield c


def _stub(monkeypatch, action: Any = None) -> list[str]:
    """打桩 acompletion:记录调用模型,返回 response() 或抛 action 给的异常。"""
    calls: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs["model"])
        raised = action() if callable(action) else action
        if isinstance(raised, BaseException):
            raise raised
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    return calls


def _unavailable(model: str) -> Exception:
    return litellm.ServiceUnavailableError(
        message=f"{model} 503", llm_provider="deepseek", model=model
    )


def test_supports_images_delegates_to_litellm(monkeypatch) -> None:
    monkeypatch.setattr(litellm, "supports_vision", lambda model: model == CHAT)

    assert vision.supports_images(CHAT) is True
    assert vision.supports_images(TEXT_ONLY) is False


def test_register_vision_models_writes_only_vision_flag(monkeypatch) -> None:
    """只补 supports_vision:价格/上下文等既有字段原样保留,原条目不被改写。"""
    registered: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(litellm, "supports_vision", lambda model: False)
    monkeypatch.setattr(
        litellm, "register_model", lambda models: registered.update(models)
    )
    monkeypatch.setitem(
        litellm.model_cost,
        PROBE,
        {"litellm_provider": "deepseek", "input_cost_per_token": 1e-06},
    )

    with capture_logs() as logs:
        written = vision.register_vision_models(
            [" deepseek/vision-probe ", "", "deepseek/vision-plain", "vision-noprefix"]
        )

    assert written == [PROBE, "deepseek/vision-plain", "vision-noprefix"]
    assert registered[PROBE]["input_cost_per_token"] == 1e-06
    assert registered[PROBE]["supports_vision"] is True
    # 无既有条目:按模型前缀补 litellm_provider;无前缀的模型不猜 provider
    assert registered["deepseek/vision-plain"] == {
        "supports_vision": True,
        "litellm_provider": "deepseek",
    }
    assert registered["vision-noprefix"] == {"supports_vision": True}
    # 复制而非改写 litellm 自带条目(否则 reload 后能力表被就地污染)
    assert "supports_vision" not in litellm.model_cost[PROBE]
    assert [item["event"] for item in logs] == ["llm_vision_models"]
    assert logs[0]["models"] == written


def test_register_vision_models_skips_already_supported(monkeypatch) -> None:
    monkeypatch.setattr(litellm, "supports_vision", lambda model: model == FLASH)
    monkeypatch.setattr(
        litellm,
        "register_model",
        lambda models: pytest.fail(f"不该注册已支持的模型:{models}"),
    )

    assert vision.register_vision_models([FLASH, ""]) == []


def test_create_app_registers_configured_vision_models(monkeypatch) -> None:
    registered: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(litellm, "supports_vision", lambda model: False)
    monkeypatch.setattr(
        litellm, "register_model", lambda models: registered.update(models)
    )
    settings = build_settings(
        Settings,
        available_models=[],
        fallback_models=[],
        vision_models=[PROBE],
    )

    create_app(settings)

    assert registered[PROBE]["supports_vision"] is True


def test_require_image_support_rejects_text_model() -> None:
    with pytest.raises(AppError) as excinfo:
        vision.require_image_support(TEXT_ONLY, declared=[CHAT])

    error = excinfo.value
    assert error.code == vision.MODEL_NO_VISION
    assert TEXT_ONLY in error.message
    assert error.details == {"model": TEXT_ONLY, "vision_models": [CHAT]}


def test_require_image_support_passes_vision_model(monkeypatch) -> None:
    monkeypatch.setattr(vision, "supports_images", lambda model: True)

    vision.require_image_support(CHAT, declared=[CHAT])  # 不抛即通过


def test_image_request_to_text_model_is_400(monkeypatch) -> None:
    """早于开流:400 明确报错,绝不把图片折叠成文本交给模型。"""
    calls = _stub(monkeypatch)
    with _client() as client:
        resp = client.post("/v1/completions", json=_body(TEXT_ONLY, _content()))

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == vision.MODEL_NO_VISION
    assert body["details"]["model"] == TEXT_ONLY
    assert calls == []


def test_stream_image_request_to_text_model_is_400(monkeypatch) -> None:
    calls = _stub(monkeypatch)
    with _client() as client:
        resp = client.post("/v1/completions/stream", json=_body(TEXT_ONLY, _content()))

    assert resp.status_code == 400
    assert resp.json()["code"] == vision.MODEL_NO_VISION
    assert calls == []


def test_text_request_to_text_model_passes(monkeypatch) -> None:
    """不带图的请求不受视觉能力影响:reasoner 照常回答问题。"""
    calls = _stub(monkeypatch)
    with _client() as client:
        resp = client.post("/v1/completions", json=_body(TEXT_ONLY, "纯文本提问"))

    assert resp.status_code == 200
    assert calls == [TEXT_ONLY]


def test_candidates_drop_text_only_models_when_images_present(monkeypatch) -> None:
    monkeypatch.setattr(vision, "supports_images", lambda model: model == CHAT)
    settings = build_settings(
        Settings,
        available_models=[],
        fallback_models=[TEXT_ONLY, FLASH],
        vision_models=[CHAT],
    )

    assert candidates(settings, CHAT) == [CHAT, TEXT_ONLY, FLASH]
    assert candidates(settings, CHAT, requires_vision=True) == [CHAT]


def test_image_request_never_falls_back_to_text_model(monkeypatch) -> None:
    """主模型 503 时宁可 502:切到文本模型会静默丢图(不给假答案)。"""
    calls = _stub(monkeypatch, lambda: _unavailable(CHAT))
    with _client(available_models=[CHAT, TEXT_ONLY], fallback_models=[TEXT_ONLY]) as c:
        resp = c.post("/v1/completions", json=_body(CHAT, _content()))

    assert resp.status_code == 502
    assert calls == [CHAT]  # 备用文本模型一次都没被调用
    assert [attempt["model"] for attempt in resp.json()["details"]["attempts"]] == [
        CHAT
    ]


def test_service_layer_rejects_images_on_text_model() -> None:
    """绕过 HTTP 直接调服务层同样拒绝,而不是把链过滤空后崩溃。"""
    settings = build_settings(
        Settings,
        available_models=[],
        fallback_models=[],
        vision_models=[CHAT],
    )
    request = CompletionRequest(
        model=TEXT_ONLY,
        messages=[Message(role="user", content=_content())],
    )

    with pytest.raises(AppError) as excinfo:
        asyncio.run(completions.complete(settings, request))
    assert excinfo.value.code == vision.MODEL_NO_VISION

    async def drain() -> None:
        async for _ in completions.stream(settings, request):
            pass

    with pytest.raises(AppError) as excinfo:
        asyncio.run(drain())
    assert excinfo.value.code == vision.MODEL_NO_VISION
