"""P1-G 模型路由/回退/多模态(契约见 docs/design/model-routing.md)。

覆盖 §6.1 模型解析优先级与 contextvar 绑定、§6.2 网关回退事件、§6.3 /models
与白名单、§6.4 triage 的 response_format、§6.5 工具参数校验、§7 图片 parts 预算。
本文件承载原 ``test_model_routing.py`` 拆分后的模型解析、绑定与网关回退部分。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from agent_orchestrator.services.llm import (
    GatewayClient,
    LLMReply,
    bind_fallback_observer,
    bind_model,
    current_model,
    notify_fallbacks,
    reset_fallback_observer,
    reset_model,
)
from tests.conftest import TENANT, FakeLLM, _client

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


# --------------------------------------------------------------------- §6.1 模型解析


async def test_gateway_model_precedence() -> None:
    """调用参数 > contextvar(P1-G 绑定)> 构造配置(LLM_MODEL);全空则不带 model 键。"""
    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"content": "ok"})

    configured = _gateway(handler)
    configured.model = "from-config"
    bare = _gateway(handler)
    token = bind_model("from-context")
    try:
        assert current_model() == "from-context"
        await configured.complete([{"role": "user", "content": "hi"}])
        await configured.complete([{"role": "user", "content": "hi"}], model="explicit")
        await bare.complete([{"role": "user", "content": "hi"}])
    finally:
        reset_model(token)
    await bare.complete([{"role": "user", "content": "hi"}])
    await configured.aclose()
    await bare.aclose()

    assert [payload.get("model") for payload in payloads] == [
        "from-context",
        "explicit",
        "from-context",
        None,
    ]
    assert current_model() is None


def test_run_binding_uses_run_model(tmp_path: Path) -> None:
    """run 路径:plan/approve 的模型绑定取 run.model 快照(§6.1)。"""
    fake = RecordingLLM()
    with _client(tmp_path, fake) as client:
        run = _create_run(client, model="run-model")
        assert run["model"] == "run-model"
        planned = client.post(f"/api/v1/runs/{run['id']}/plan", headers=TENANT)
        assert planned.status_code == 200
        assert set(fake.context_models) == {"run-model"}

        approved = client.post(f"/api/v1/runs/{run['id']}/approve", headers=TENANT)
        assert approved.status_code == 200
        # 审批恢复的图调用同样在绑定作用域内
        assert fake.context_models[-1] == "run-model"


def test_dialogue_binding_prefers_message_model(tmp_path: Path) -> None:
    """对话路径:message.model > conversation.model(§6.1/§6.3)。"""
    fake = RecordingLLM()
    with _client(tmp_path, fake) as client:
        created = client.post(
            "/api/v1/conversations", json={"model": "conv-model"}, headers=TENANT
        )
        assert created.status_code == 201
        assert created.json()["model"] == "conv-model"
        conversation_id = created.json()["id"]

        first = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "你好", "mode": "chat", "model": "msg-model"},
            headers=TENANT,
        )
        assert first.status_code == 201
        assert fake.context_models[-1] == "msg-model"

        second = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "再聊一句", "mode": "chat"},
            headers=TENANT,
        )
        assert second.status_code == 201
        assert fake.context_models[-1] == "conv-model"


# --------------------------------------------------------------------- §6.2 回退


async def test_gateway_forwards_response_format_and_reports_fallbacks() -> None:
    payloads: list[dict[str, Any]] = []
    hops: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "content": "{}",
                "fallbacks": [
                    {
                        "from_model": "primary",
                        "to_model": "backup",
                        "code": "fallback.timeout",
                        "message": "x" * 600,
                    }
                ],
            },
        )

    client = _gateway(handler)

    async def observer(items: Any) -> None:
        hops.append(items)

    token = bind_fallback_observer(observer)
    try:
        await client.complete(
            [{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )
    finally:
        reset_fallback_observer(token)
        await client.aclose()

    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert len(hops) == 1
    assert hops[0][0].from_model == "primary"
    assert hops[0][0].to_model == "backup"
    assert hops[0][0].code == "fallback.timeout"
    # §5:回退跳的 message 截断到 500 字
    assert len(hops[0][0].message) == 500


async def test_gateway_stream_reports_fallbacks_on_done_frame() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        done = {"done": True, "fallbacks": [{"from_model": "a", "to_model": "b"}]}
        body = f'data: {{"delta": "hi"}}\n\ndata: {json.dumps(done)}\n\n'
        return httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )

    client = _gateway(handler)
    hops: list[Any] = []

    async def observer(items: Any) -> None:
        hops.append(items)

    token = bind_fallback_observer(observer)
    try:
        texts = [event.text async for event in client.stream_events([{"role": "user"}])]
    finally:
        reset_fallback_observer(token)
        await client.aclose()

    assert texts == ["hi"]
    assert hops and hops[0][0].to_model == "b"


def test_run_records_fallback_event(tmp_path: Path) -> None:
    """run 路径回退落 run.llm.fallback.v1(§6.2),不进 outbox。"""
    fake = RecordingLLM(
        fallbacks=[
            {
                "from_model": "a",
                "to_model": "b",
                "code": "fallback.timeout",
                "message": "超时",
            }
        ]
    )
    with _client(tmp_path, fake) as client:
        run = _create_run(client, model="a")
        assert (
            client.post(f"/api/v1/runs/{run['id']}/plan", headers=TENANT).status_code
            == 200
        )
        events = client.get(f"/api/v1/runs/{run['id']}/events", headers=TENANT).json()

    fallbacks = [event for event in events if event["type"] == "run.llm.fallback.v1"]
    assert fallbacks
    payload = fallbacks[0]["payload"]
    assert payload["requested_model"] == "a"
    assert payload["resolved_model"] == "b"
    assert payload["hops"][0]["code"] == "fallback.timeout"
