"""test_hooks_mounts_* 的共享夹具:MockTransport hook server、应用装配与断言助手。

从 test_hooks_mounts.py 原样搬出(零网络:出站 hook 请求走 httpx.MockTransport,
DNS 由 fake 解析器给公网地址);按主题拆分的各测试模块显式 import 本模块。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import hooks as hooks_service
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import (
    ToolRegistry,
    ToolRisk,
    ToolSpec,
    build_tools,
)
from tests.conftest import (
    ACCOUNT,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _settings,
    verifier,
)

PUBLIC_ADDRESS = "93.184.216.34"
HOOK_TARGET = "https://hooks.example.com/dsh"
WRITE_TOOL = "notes__create"
VERIFY_PASS = '{"summary": "已完成", "verdicts": {"日程已整理": true}}'
DANGER_ARGS = '{"title": "对外发布说明"}'


def _resolver(host: str) -> list[str]:
    return [PUBLIC_ADDRESS]


Responder = Callable[[dict[str, Any], int], Any]


class _HookServer:
    """MockTransport 处理器:记录请求体 + 按用例给响应(或抛异常)。"""

    def __init__(self, responder: Responder | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self._responder: Responder = responder or (
            lambda body, index: {"decision": "allow"}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(request)
        self.bodies.append(body)
        reply = self._responder(body, len(self.bodies) - 1)
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json=reply)


@contextmanager
def _hooks_client(
    tmp_path: Path,
    *,
    server: _HookServer,
    replies: list[Any] | None = None,
    fake: FakeLLM | None = None,
    write_seen: list[dict[str, Any]] | None = None,
    knowledge: FakeKnowledge | None = None,
    extras: tuple[ToolSpec, ...] = (),
    **overrides: Any,
) -> Iterator[TestClient]:
    settings = _settings(
        tmp_path,
        "hooks-mounts.db",
        llm_enabled=True,
        tools_enabled=True,
        hooks_enabled=True,
        **overrides,
    )
    knowledge = knowledge or FakeKnowledge()
    registry: ToolRegistry | None = None
    if write_seen is not None:
        registry = ToolRegistry([_write_spec(write_seen)])

    def toolkit_factory(account: str) -> Any:
        if registry is not None:
            return registry
        return build_tools(account, knowledge=knowledge).extended(list(extras))

    app = create_app(
        settings,
        llm_client=fake if fake is not None else FakeLLM(list(replies or [])),
        toolkit_factory=toolkit_factory,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        # 容器装配阶段已用纯 settings 配过一次;这里换成打桩 transport + 解析器。
        hooks_service.configure_hooks(
            settings,
            transport=httpx.MockTransport(server),
            resolver=_resolver,
        )
        yield client


def _install_hook(
    client: TestClient, event: str, *, matcher: str = "*"
) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/hooks",
        json={"event": event, "matcher": matcher, "target": HOOK_TARGET},
        headers=ACCOUNT,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _write_spec(seen: list[dict[str, Any]]) -> ToolSpec:
    async def handler(arguments: dict) -> str:
        seen.append(arguments)
        return json.dumps(
            {"created": True, "title": arguments.get("title")}, ensure_ascii=False
        )

    return ToolSpec(
        name=WRITE_TOOL,
        description="写笔记",
        parameters={"type": "object"},
        handler=handler,
        risk=ToolRisk.WRITE,
    )


def _read_call() -> LLMReply:
    return LLMReply(
        content="",
        tool_calls=(
            ToolCall(
                id="call_read", name="search_knowledge", arguments='{"query": "日程"}'
            ),
        ),
    )


def _danger_call() -> LLMReply:
    return LLMReply(
        content="",
        tool_calls=(
            ToolCall(id="call_w1", name="external_publish", arguments=DANGER_ARGS),
        ),
    )


def _write_call() -> LLMReply:
    return LLMReply(
        content="",
        tool_calls=(
            ToolCall(id="call-1", name=WRITE_TOOL, arguments='{"title": "周报"}'),
        ),
    )


def _run_with_tool(client: TestClient) -> tuple[str, list[dict[str, Any]]]:
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
    run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
    events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()
    assert run["status"] == "completed", run
    return run_id, events


def _tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e["payload"] for e in events if e["type"] == "run.tool.called.v1"]


def _sse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in text.split("\n\n"):
        name = data = None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if name and data:
            events.append((name, json.loads(data)))
    return events


def _chat_stream(
    client: TestClient, conversation_id: str
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "帮我处理一下", "mode": "chat"},
        headers=ACCOUNT,
    )
    assert resp.status_code == 200, resp.text
    events = _sse_events(resp.text)
    tool_frames = [data for name, data in events if name == "tool"]
    assert len(tool_frames) == 1, events
    return events, tool_frames[0]


def _create_conversation(client: TestClient) -> str:
    resp = client.post("/api/v1/conversations", json={}, headers=ACCOUNT)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _tool_messages(fake: FakeLLM) -> list[dict[str, Any]]:
    """模型实际看到的 tool 消息(跨所有轮次,按时间顺序)。"""
    return [
        message
        for call in fake.calls
        for message in call
        if message.get("role") == "tool"
    ]
