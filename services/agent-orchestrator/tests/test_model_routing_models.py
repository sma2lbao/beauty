"""P1-G 模型路由/回退/多模态(契约见 docs/design/model-routing.md)。

覆盖 §6.1 模型解析优先级与 contextvar 绑定、§6.2 网关回退事件、§6.3 /models
与白名单、§6.4 triage 的 response_format、§6.5 工具参数校验、§7 图片 parts 预算。
本文件承载原 ``test_model_routing.py`` 拆分后的 /models 投影、白名单、triage
与图片预算部分。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import history, triage
from agent_orchestrator.services import models as models_service
from agent_orchestrator.services.llm import (
    GatewayClient,
    LLMReply,
    current_model,
    notify_fallbacks,
)
from agent_orchestrator.services.tools import ToolRegistry, ToolSpec
from tests.conftest import _IDP, TENANT, FakeLLM, _client, _settings

#: 1x1 PNG(内容本身不重要,只验证 base64 往返与 parts 形状)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


class RecordingLLM(FakeLLM):
    """FakeLLM 派生:记录每次调用的当前绑定模型;回复固定(不限次数)。"""

    def __init__(
        self,
        *,
        fallbacks: list[dict[str, Any]] | None = None,
        reply: str = "# 计划\n\n- [ ] 做",
    ) -> None:
        super().__init__([], fallbacks=fallbacks)
        self.reply = reply
        self.context_models: list[str | None] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMReply:
        self.context_models.append(current_model())
        self.calls.append(messages)
        self.tools_seen.append(tools)
        self.models.append(model)
        self.response_formats.append(response_format)
        if self.fallbacks:
            await notify_fallbacks(self.fallbacks)
        return LLMReply(content=self.reply)


def _gateway(handler) -> GatewayClient:
    return GatewayClient("http://gateway", transport=httpx.MockTransport(handler))


def _create_run(client: TestClient, **extra: Any) -> dict[str, Any]:
    body = {"title": "周报", "goal": "整理本周日程", **extra}
    resp = client.post("/api/v1/runs", json=body, headers=TENANT)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------- §6.3 /models


async def test_gateway_list_models_projection_and_failure() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "default_model": "gw-default",
                "data": [
                    {"id": "m1", "owned_by": "beauty-moon"},
                    {"id": "m2"},
                ],
            },
        )

    client = _gateway(ok)
    assert await client.list_models() == [
        {"id": "m1", "owned_by": "beauty-moon"},
        {"id": "m2", "owned_by": "beauty-moon"},
    ]
    await client.aclose()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    unreachable = _gateway(boom)
    assert await unreachable.list_models() is None
    await unreachable.aclose()


def test_models_endpoint_projects_gateway(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "default_model": "gw-default",
                "data": [{"id": "m1", "owned_by": "beauty-moon"}],
            },
        )

    app = create_app(
        _settings(tmp_path, "models.db", llm_enabled=True, llm_model="m1"),
        llm_client=_gateway(handler),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        resp = client.get("/api/v1/models", headers=TENANT)

    assert resp.status_code == 200
    assert resp.json() == {
        "default_model": "m1",
        "models": [{"id": "m1", "owned_by": "beauty-moon"}],
    }


def test_models_endpoint_falls_back_to_settings(tmp_path: Path) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    app = create_app(
        _settings(
            tmp_path,
            "models-fallback.db",
            llm_enabled=True,
            llm_available_models=["a", "b"],
        ),
        llm_client=_gateway(boom),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        resp = client.get("/api/v1/models", headers=TENANT)

    assert resp.status_code == 200
    assert resp.json() == {
        "default_model": None,
        "models": [
            {"id": "a", "owned_by": "settings"},
            {"id": "b", "owned_by": "settings"},
        ],
    }


async def test_models_cache_reuses_gateway_result(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    client = _gateway(handler)
    settings = _settings(
        tmp_path, "cache.db", llm_enabled=True, llm_models_cache_seconds=60.0
    )
    try:
        first = await models_service.available_models(client, settings)
        second = await models_service.available_models(client, settings)
        assert first == second
        assert len(calls) == 1

        models_service.reset_models_cache()
        await models_service.available_models(client, settings)
        assert len(calls) == 2
    finally:
        await client.aclose()


def test_model_not_allowed_is_400(client: TestClient, tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path, "allow.db", llm_available_models=["allowed"]),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as guarded:
        run = guarded.post(
            "/api/v1/runs",
            json={"title": "t", "goal": "g", "model": "other"},
            headers=TENANT,
        )
        assert run.status_code == 400
        assert run.json()["code"] == "llm.model_not_allowed"

        conversation = guarded.post(
            "/api/v1/conversations", json={"model": "other"}, headers=TENANT
        )
        assert conversation.status_code == 400
        assert conversation.json()["code"] == "llm.model_not_allowed"

        allowed = guarded.post(
            "/api/v1/conversations", json={"model": "allowed"}, headers=TENANT
        )
        assert allowed.status_code == 201
        assert allowed.json()["model"] == "allowed"

        updated = guarded.patch(
            f"/api/v1/conversations/{allowed.json()['id']}",
            json={"model": "other"},
            headers=TENANT,
        )
        assert updated.status_code == 400

        message = guarded.post(
            f"/api/v1/conversations/{allowed.json()['id']}/messages",
            json={"content": "你好", "mode": "chat", "model": "other"},
            headers=TENANT,
        )
        assert message.status_code == 400
        assert message.json()["code"] == "llm.model_not_allowed"


# --------------------------------------------------------------------- §6.4 triage


def test_triage_uses_json_schema_response_format(tmp_path: Path) -> None:
    fake = FakeLLM(['{"mode": "chat"}', "你好!有什么可以帮你的?"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=TENANT
        ).json()["id"]
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "你好"},
            headers=TENANT,
        )

    assert resp.status_code == 201
    assert resp.json()["mode"] == "chat"
    assert fake.response_formats[0] == triage.TRIAGE_RESPONSE_FORMAT


# --------------------------------------------------------------------- §6.5 工具参数


async def test_tool_registry_rejects_invalid_arguments() -> None:
    seen: list[dict[str, Any]] = []

    async def handler(args: dict[str, Any]) -> str:
        seen.append(args)
        return "ok"

    registry = ToolRegistry(
        (
            ToolSpec(
                name="publish",
                description="发布",
                parameters={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
                handler=handler,
            ),
        )
    )
    rejected = await registry.execute("publish", "{}")
    payload = json.loads(rejected)
    assert payload["error"].startswith("工具参数无效")
    assert seen == []

    assert await registry.execute("publish", json.dumps({"title": "x"})) == "ok"
    assert seen == [{"title": "x"}]


# --------------------------------------------------------------------- §7 多模态


def _upload_image(client: TestClient, conversation_id: str, name: str) -> str:
    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": (name, PNG, "image/png")},
        headers=TENANT,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_chat_prompt_carries_image_parts(tmp_path: Path) -> None:
    fake = FakeLLM(["看到了"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=TENANT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "看一下这张图",
                "mode": "chat",
                "attachment_ids": [image_id],
            },
            headers=TENANT,
        )

    assert resp.status_code == 201
    prompt = fake.calls[0]
    content = prompt[-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "看一下这张图"}
    assert content[1]["type"] == "image_url"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG


def test_image_budget_limits_parts(tmp_path: Path) -> None:
    fake = FakeLLM(["看到了"])
    app = create_app(
        _settings(tmp_path, "img.db", llm_enabled=True, llm_image_max_count=1),
        llm_client=fake,
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=TENANT
        ).json()["id"]
        first = _upload_image(client, conversation_id, "a.png")
        second = _upload_image(client, conversation_id, "b.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "两张图",
                "mode": "chat",
                "attachment_ids": [first, second],
            },
            headers=TENANT,
        )

    assert resp.status_code == 201
    content = fake.calls[0][-1]["content"]
    assert isinstance(content, list)
    assert sum(part.get("type") == "image_url" for part in content) == 1


async def test_history_prompt_inlines_images() -> None:
    rows = [
        SimpleNamespace(id="m1", role="user", content="看一下图"),
        SimpleNamespace(id="m2", role="assistant", content="好的"),
    ]
    parts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]
    prompt = history.to_prompt(rows, images_by_message={"m1": parts})

    assert prompt[0]["content"] == [{"type": "text", "text": "看一下图"}, *parts]
    assert prompt[1] == {"role": "assistant", "content": "好的"}
