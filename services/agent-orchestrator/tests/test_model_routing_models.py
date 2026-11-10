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
import pytest
from common.errors import ConflictError
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
from tests.conftest import _IDP, ACCOUNT, FakeLLM, _client, _settings
from tests.support.conversations import _sse_events

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
    resp = client.post("/api/v1/runs", json=body, headers=ACCOUNT)
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
        resp = client.get("/api/v1/models", headers=ACCOUNT)

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
        resp = client.get("/api/v1/models", headers=ACCOUNT)

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
            headers=ACCOUNT,
        )
        assert run.status_code == 400
        assert run.json()["code"] == "llm.model_not_allowed"

        conversation = guarded.post(
            "/api/v1/conversations", json={"model": "other"}, headers=ACCOUNT
        )
        assert conversation.status_code == 400
        assert conversation.json()["code"] == "llm.model_not_allowed"

        allowed = guarded.post(
            "/api/v1/conversations", json={"model": "allowed"}, headers=ACCOUNT
        )
        assert allowed.status_code == 201
        assert allowed.json()["model"] == "allowed"

        updated = guarded.patch(
            f"/api/v1/conversations/{allowed.json()['id']}",
            json={"model": "other"},
            headers=ACCOUNT,
        )
        assert updated.status_code == 400

        message = guarded.post(
            f"/api/v1/conversations/{allowed.json()['id']}/messages",
            json={"content": "你好", "mode": "chat", "model": "other"},
            headers=ACCOUNT,
        )
        assert message.status_code == 400
        assert message.json()["code"] == "llm.model_not_allowed"


# --------------------------------------------------------------------- §6.4 triage


def test_triage_uses_json_schema_response_format(tmp_path: Path) -> None:
    fake = FakeLLM(['{"mode": "chat"}', "你好!有什么可以帮你的?"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "你好"},
            headers=ACCOUNT,
        )

    assert resp.status_code == 201
    assert resp.json()["mode"] == "chat"
    assert fake.response_formats[0] == triage.TRIAGE_RESPONSE_FORMAT


async def test_triage_with_images_skips_model_and_goes_chat() -> None:
    """带图消息不进模型判别:任务路径不注入图片,判成 task 就等于丢图。"""
    fake = FakeLLM(['{"mode": "task"}'])
    assert await triage.decide("看一下这张图", fake, has_images=True) == triage.CHAT
    assert fake.calls == []


async def test_triage_without_images_still_asks_model() -> None:
    fake = FakeLLM(['{"mode": "task"}'])
    assert await triage.decide("整理成周报", fake) == triage.TASK
    assert fake.response_formats[0] == triage.TRIAGE_RESPONSE_FORMAT


async def test_triage_rejects_task_mode_with_images() -> None:
    with pytest.raises(ConflictError) as excinfo:
        await triage.decide("整理成周报", None, mode=triage.TASK, has_images=True)
    assert excinfo.value.code == "task.image_unsupported"


async def test_triage_with_images_without_llm_is_conflict() -> None:
    """没有模型时两条路都看不见图:带图 auto 也必须如实 409,不能丢进任务。"""
    with pytest.raises(ConflictError) as excinfo:
        await triage.decide("看一下这张图", None, has_images=True)
    assert excinfo.value.code == "chat.llm_disabled"


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
        headers=ACCOUNT,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_chat_prompt_carries_image_parts(tmp_path: Path) -> None:
    fake = FakeLLM(["看到了"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "看一下这张图",
                "mode": "chat",
                "attachment_ids": [image_id],
            },
            headers=ACCOUNT,
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
            "/api/v1/conversations", json={}, headers=ACCOUNT
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
            headers=ACCOUNT,
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

# ------------------------------------------------------- §7 带图分流(任务不注入图片)


def test_auto_message_with_image_goes_chat_without_triage(tmp_path: Path) -> None:
    """auto + 带图:任务路径不注入图片,所以直接直答,不调分流判别。"""
    fake = FakeLLM(["红色"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "这张图是什么颜色?", "attachment_ids": [image_id]},
            headers=ACCOUNT,
        )

    assert resp.status_code == 201
    assert resp.json()["mode"] == "chat"
    assert resp.json()["run"] is None
    # 没有分流判别调用(json_schema),第一次调用就是多模态直答
    assert triage.TRIAGE_RESPONSE_FORMAT not in fake.response_formats
    content = fake.calls[0][-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "这张图是什么颜色?"}
    assert content[1]["type"] == "image_url"


def test_task_mode_with_image_is_rejected(tmp_path: Path) -> None:
    """显式任务模式带图 → 409:任务路径看不见图,静默丢图比报错更糟。"""
    fake = FakeLLM(["不该被调用"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "整理成周报",
                "mode": "task",
                "attachment_ids": [image_id],
            },
            headers=ACCOUNT,
        )

    assert resp.status_code == 409
    assert resp.json()["code"] == "task.image_unsupported"
    assert fake.calls == []


def test_stream_task_mode_with_image_is_rejected_before_streaming(
    tmp_path: Path,
) -> None:
    """SSE 开流前就 409:一旦 200 开流,只能塞一帧错误。"""
    fake = FakeLLM(["不该被调用"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={
                "content": "整理成周报",
                "mode": "task",
                "attachment_ids": [image_id],
            },
            headers=ACCOUNT,
        )

    assert resp.status_code == 409
    assert resp.json()["code"] == "task.image_unsupported"
    assert fake.calls == []


def test_stream_auto_message_with_image_reports_chat(tmp_path: Path) -> None:
    fake = FakeLLM(["红色"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"content": "这张图是什么颜色?", "attachment_ids": [image_id]},
            headers=ACCOUNT,
        )
        events = _sse_events(resp.text)

    assert resp.status_code == 200
    meta = next(data for name, data in events if name == "meta")
    assert meta["mode"] == "chat"
    assert meta["run"] is None
    done = next(data for name, data in events if name == "done")
    assert done["reply"]["content"] == "红色"

def test_auto_message_with_image_without_llm_is_conflict(client: TestClient) -> None:
    """没有模型时带图消息 409:任务路径不注入图片,不能悄悄把图丢掉。"""
    conversation_id = client.post(
        "/api/v1/conversations", json={}, headers=ACCOUNT
    ).json()["id"]
    image_id = _upload_image(client, conversation_id, "pic.png")

    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "这张图是什么颜色?", "attachment_ids": [image_id]},
        headers=ACCOUNT,
    )

    assert resp.status_code == 409
    assert resp.json()["code"] == "chat.llm_disabled"


def test_stream_auto_message_with_image_without_llm_is_409_before_stream(
    client: TestClient,
) -> None:
    conversation_id = client.post(
        "/api/v1/conversations", json={}, headers=ACCOUNT
    ).json()["id"]
    image_id = _upload_image(client, conversation_id, "pic.png")

    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "这张图是什么颜色?", "attachment_ids": [image_id]},
        headers=ACCOUNT,
    )

    assert resp.status_code == 409
    assert resp.json()["code"] == "chat.llm_disabled"
