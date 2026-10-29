"""多模态请求(model-routing §5.1):ContentPart 透传、文本/图片上限、URL 协议。

结构校验由 pydantic(422);数量/长度/URL 协议属业务约束,入口层校验后返回 400
且必须发生在开流之前。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import litellm
import pytest
from common.service_testkit import app_client, build_settings
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.schemas.completions import (
    CompletionRequest,
    Message,
    image_urls,
    text_chars,
)
from llm_gateway.services import completions
from tests.fakes import chunk, response, stream

PNG = "data:image/png;base64,iVBORw0KGgo="
TEXT = "这张图里有什么?"


def _content(text: str = TEXT, url: str = PNG) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": url}},
    ]


def _body(*, text: str | None = TEXT, url: str = PNG) -> dict[str, Any]:
    content = text if text is None else _content(text, url)
    return {"messages": [{"role": "user", "content": content}]}


@contextmanager
def _client(**overrides: Any) -> Iterator[TestClient]:
    settings = build_settings(
        Settings, available_models=[], fallback_models=[], **overrides
    )
    with app_client(create_app(settings)) as client:
        yield client


def _stub(monkeypatch) -> dict:
    captured: dict = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    return captured


def test_multimodal_content_passthrough(client: TestClient, monkeypatch) -> None:
    captured = _stub(monkeypatch)
    resp = client.post("/v1/completions", json=_body())

    assert resp.status_code == 200
    sent = captured["messages"][0]["content"]
    assert sent == _content()  # 形状原样透传 litellm,不做重排/降级


@pytest.mark.parametrize(
    "url", ["https://example.com/cat.png", "http://example.com/a.webp"]
)
def test_http_image_url_allowed(client: TestClient, monkeypatch, url: str) -> None:
    captured = _stub(monkeypatch)
    resp = client.post("/v1/completions", json=_body(url=url))

    assert resp.status_code == 200
    assert captured["messages"][0]["content"][1]["image_url"]["url"] == url


def test_text_limit_exceeded_is_400(client: TestClient, monkeypatch) -> None:
    called: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        called.append("called")
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    # 一条消息是 str、一条是 ContentPart 列表,两处文本都要计入总长
    payload = {
        "messages": [
            {"role": "user", "content": "x" * 60000},
            {"role": "user", "content": _content("y" * 40001)},
        ]
    }
    resp = client.post("/v1/completions", json=payload)

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "llm.invalid_message"
    assert body["details"]["chars"] == 100001
    assert body["details"]["max_chars"] == 100000
    assert called == []


def test_text_limit_boundary_passes() -> None:
    """恰好 100000 字合法(上限是 > 才拒绝)。"""
    from llm_gateway.schemas.completions import validate_request

    request = CompletionRequest(messages=[Message(role="user", content="x" * 100000)])
    validate_request(request, max_images=8, max_image_data_chars=8_000_000)


def test_too_many_images_is_400(monkeypatch) -> None:
    called: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        called.append("called")
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": PNG}} for _ in range(3)
                ],
            }
        ]
    }
    with _client(max_images_per_request=2) as client:
        resp = client.post("/v1/completions", json=payload)

    assert resp.status_code == 400
    assert resp.json()["code"] == "llm.invalid_message"
    assert resp.json()["details"]["max_images"] == 2
    assert called == []


def test_bad_url_scheme_is_400(client: TestClient) -> None:
    resp = client.post("/v1/completions", json=_body(url="ftp://example.com/a.png"))

    assert resp.status_code == 400
    assert resp.json()["code"] == "llm.invalid_message"
    assert resp.json()["details"]["index"] == 0


def test_non_base64_data_url_is_400(client: TestClient) -> None:
    resp = client.post(
        "/v1/completions", json=_body(url="data:image/png;notbase64,abc")
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "llm.invalid_message"


def test_image_data_url_too_long_is_400(monkeypatch) -> None:
    called: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        called.append("called")
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client(max_image_data_chars=20) as client:
        resp = client.post("/v1/completions", json=_body())

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "llm.invalid_message"
    assert body["details"]["max_chars"] == 20
    assert called == []


def test_invalid_part_shape_is_422(client: TestClient) -> None:
    resp = client.post(
        "/v1/completions",
        json={
            "messages": [{"role": "user", "content": [{"type": "audio", "audio": {}}]}]
        },
    )

    assert resp.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "x" * 100001}],
                }
            ]
        },
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "ftp://x"}}],
                }
            ]
        },
    ],
)
def test_stream_validation_is_400_before_sse(
    client: TestClient, monkeypatch, payload
) -> None:
    called: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        called.append("called")
        return await stream([chunk("x")])(**kwargs)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    resp = client.post("/v1/completions/stream", json=payload)

    assert resp.status_code == 400
    assert resp.json()["code"] == "llm.invalid_message"
    assert called == []


def test_text_chars_and_image_urls_helpers() -> None:
    messages = [
        Message(role="system", content="提示"),
        Message(role="user", content=_content("你好", "https://a.test/1.png")),
        Message(role="assistant", content=None),
    ]
    assert [text_chars(message) for message in messages] == [2, 2, 0]
    assert image_urls(messages) == ["https://a.test/1.png"]


def test_log_content_previews_images_without_payload(monkeypatch) -> None:
    """LOG_CONTENT 打开时图片只记占位符,不能把 base64 正文灌进日志。"""
    captured: dict = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    settings = build_settings(
        Settings,
        available_models=[],
        fallback_models=[],
        log_content=True,
    )
    request = CompletionRequest(
        model="prov-a/vision",
        messages=[
            Message(role="user", content=_content()),
            Message(role="assistant", content=None),
        ],
    )
    with capture_logs() as logs:
        asyncio.run(completions.complete(settings, request))

    content_logs = [item for item in logs if item["event"] == "llm_content"]
    assert len(content_logs) == 2  # 请求 + 响应
    dumped = json.dumps(content_logs, ensure_ascii=False, default=str)
    assert "data:image" not in dumped
    assert "[image]" in dumped
    assert content_logs[0]["payload"][0]["content"] == f"{TEXT} | [image]"
    assert content_logs[0]["payload"][1]["content"] == ""
