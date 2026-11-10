"""P1-B 应用级 hooks 的挂点行为(docs/design/hooks.md §6/§10)。

覆盖四个工具执行挂点(图内自动分支、人工确认后重放、对话 READ/WRITE 执行、
对话确认端点)与会话/压缩事件,并钉住顺序约束:PreToolUse 先于审批策略
(deny 不产生 pending)、PostToolUse 在真正执行之后只向模型可见内容追加 note。

零网络:出站 hook 请求走 httpx.MockTransport(响应由用例按请求体决定),
DNS 由 fake 解析器给公网地址,不触网。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from database.base import Base
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.models.hook import AccountHook
from agent_orchestrator.repositories import HookRepository
from agent_orchestrator.services import compaction
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
    FakeDangerTool,
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


# ---------------------------------------------------------------- 图:自动分支


def test_graph_pre_tool_use_deny_short_circuits_read_tool(tmp_path: Path) -> None:
    server = _HookServer(lambda body, index: {"decision": "deny", "reason": "维护窗口"})
    knowledge = FakeKnowledge()
    replies = ["# 计划", _read_call(), "检索被拒绝,直接回答", VERIFY_PASS, "# 交付物"]
    with _hooks_client(
        tmp_path, server=server, replies=replies, knowledge=knowledge
    ) as client:
        _install_hook(client, "PreToolUse")
        run_id, events = _run_with_tool(client)

    # 工具没有执行,错误回喂给模型;审计记 hook_denied
    assert knowledge.queries == []
    called = _tool_calls(events)
    assert len(called) == 1
    assert called[0]["name"] == "search_knowledge"
    assert called[0]["ok"] is False
    assert called[0]["decision"] == "hook_denied"
    assert "hook denied: 维护窗口" in called[0]["detail"]

    body = server.bodies[0]
    assert body["event"] == "PreToolUse"
    assert body["tool"] == "search_knowledge"
    assert body["run_id"] == run_id
    assert body["arguments"] == '{"query": "日程"}'


def test_graph_pre_tool_use_deny_prevents_confirmation_gate(tmp_path: Path) -> None:
    """顺序硬约束:hook 在 approval.decide 之前,deny 不产生 pending 写。"""
    server = _HookServer(lambda body, index: {"decision": "deny", "reason": "禁止外发"})
    danger = FakeDangerTool()
    replies = ["# 计划", _danger_call(), "已放弃发布", VERIFY_PASS, "# 交付物"]
    with _hooks_client(
        tmp_path, server=server, replies=replies, extras=(danger.spec(),)
    ) as client:
        _install_hook(client, "PreToolUse", matcher="external_publish")
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
        events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()

    assert run["status"] == "completed"
    assert danger.sent == []
    types = [e["type"] for e in events]
    assert "run.confirmation.required.v1" not in types
    called = _tool_calls(events)
    assert called[0]["decision"] == "hook_denied"


def _tool_messages(fake: FakeLLM) -> list[dict[str, Any]]:
    """模型实际看到的 tool 消息(跨所有轮次,按时间顺序)。"""
    return [
        message
        for call in fake.calls
        for message in call
        if message.get("role") == "tool"
    ]


def test_graph_post_tool_use_note_lands_in_tool_result(tmp_path: Path) -> None:
    server = _HookServer(
        lambda body, index: {"decision": "allow", "note": "请核对来源"}
    )
    knowledge = FakeKnowledge()
    fake = FakeLLM(["# 计划", _read_call(), "已核对", VERIFY_PASS, "# 交付物"])
    with _hooks_client(
        tmp_path, server=server, fake=fake, knowledge=knowledge
    ) as client:
        _install_hook(client, "PostToolUse")
        _, events = _run_with_tool(client)

    assert knowledge.queries == [("t-1", "日程", 5)]
    called = _tool_calls(events)
    assert called[0]["ok"] is True
    assert called[0]["decision"] == "auto"
    # note 在 _record 之后追加,只进模型可见内容,不改审计 detail
    assert "[hook note] 请核对来源" not in called[0]["detail"]
    assert "[hook note] 请核对来源" in _tool_messages(fake)[-1]["content"]
    assert server.bodies[0]["event"] == "PostToolUse"
    assert server.bodies[0]["result"]


# ---------------------------------------------------------------- 图:确认后重放


def test_tool_exec_pre_tool_use_deny_after_manual_approval(tmp_path: Path) -> None:
    """人工放行后再次被 hook 拒绝:写操作不执行、审计记 hook_denied。"""

    def responder(body: dict[str, Any], index: int) -> dict[str, Any]:
        # 第一次(execute 节点)放行以进入确认闸,第二次(tool_exec)拒绝
        if index == 0:
            return {"decision": "allow"}
        return {"decision": "deny", "reason": "确认后策略变更"}

    server = _HookServer(responder)
    danger = FakeDangerTool()
    replies = ["# 计划", _danger_call(), "写操作被拒绝", VERIFY_PASS, "# 交付物"]
    with _hooks_client(
        tmp_path, server=server, replies=replies, extras=(danger.spec(),)
    ) as client:
        _install_hook(client, "PreToolUse", matcher="external_publish")
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        waiting = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
        assert waiting["status"] == "awaiting_confirmation"
        run = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve"},
            headers=ACCOUNT,
        ).json()
        events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()

    assert run["status"] == "completed"
    assert danger.sent == []
    called = [p for p in _tool_calls(events) if p["name"] == "external_publish"]
    assert called[0]["decision"] == "hook_denied"
    assert "hook denied: 确认后策略变更" in called[0]["detail"]
    assert [body["event"] for body in server.bodies] == [
        "PreToolUse",
        "PreToolUse",
    ]


def test_tool_exec_post_tool_use_note_lands_in_tool_result(tmp_path: Path) -> None:
    server = _HookServer(
        lambda body, index: {
            "decision": "allow",
            "note": "审计已记录" if body["event"] == "PostToolUse" else "",
        }
    )
    danger = FakeDangerTool()
    fake = FakeLLM(["# 计划", _danger_call(), "已发布", VERIFY_PASS, "# 交付物"])
    with _hooks_client(
        tmp_path, server=server, fake=fake, extras=(danger.spec(),)
    ) as client:
        _install_hook(client, "PreToolUse", matcher="external_publish")
        _install_hook(client, "PostToolUse", matcher="external_publish")
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve"},
            headers=ACCOUNT,
        )
        events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()

    assert danger.sent == [{"title": "对外发布说明"}]
    called = [p for p in _tool_calls(events) if p["name"] == "external_publish"]
    assert called[0]["decision"] == "approved"
    assert "[hook note] 审计已记录" in _tool_messages(fake)[-1]["content"]


# ---------------------------------------------------------------- 对话执行点


def test_chat_pre_tool_use_deny_write_creates_no_pending(tmp_path: Path) -> None:
    server = _HookServer(lambda body, index: {"decision": "deny", "reason": "禁止写入"})
    seen: list[dict[str, Any]] = []
    replies = [_write_call(), "没有写入权限,先给你结论"]
    with _hooks_client(
        tmp_path, server=server, replies=replies, write_seen=seen
    ) as client:
        _install_hook(client, "PreToolUse")
        conversation_id = _create_conversation(client)
        _, frame = _chat_stream(client, conversation_id)

    assert seen == []
    assert frame["decision"] == "hook_denied"
    assert frame["ok"] is False
    assert "confirmation_id" not in frame
    assert server.bodies[0]["tool"] == WRITE_TOOL
    assert server.bodies[0]["conversation_id"] == conversation_id


def test_chat_pre_tool_use_deny_read_tool_not_executed(tmp_path: Path) -> None:
    server = _HookServer(lambda body, index: {"decision": "deny", "reason": "禁用检索"})
    knowledge = FakeKnowledge()
    replies = [_read_call(), "检索被禁用"]
    with _hooks_client(
        tmp_path, server=server, replies=replies, knowledge=knowledge
    ) as client:
        _install_hook(client, "PreToolUse")
        conversation_id = _create_conversation(client)
        _, frame = _chat_stream(client, conversation_id)

    assert knowledge.queries == []
    assert frame["decision"] == "hook_denied"


def test_chat_post_tool_use_note_reaches_model_content(tmp_path: Path) -> None:
    server = _HookServer(
        lambda body, index: {"decision": "allow", "note": "注意引用来源"}
    )
    knowledge = FakeKnowledge()
    fake = FakeLLM([_read_call(), "已按提示核对"])
    with _hooks_client(
        tmp_path, server=server, fake=fake, knowledge=knowledge
    ) as client:
        _install_hook(client, "PostToolUse")
        conversation_id = _create_conversation(client)
        _, frame = _chat_stream(client, conversation_id)
        # 第二个 LLM 轮次拿到的 tool 消息里带 note
        tool_messages = [
            message for message in fake.calls[-1] if message.get("role") == "tool"
        ]

    assert knowledge.queries == [("t-1", "日程", 5)]
    assert frame["ok"] is True
    assert tool_messages[0]["content"].endswith("[hook note] 注意引用来源")


def test_confirmation_endpoint_hook_deny_keeps_pending(tmp_path: Path) -> None:
    """对话确认端点:再被 hook 拒绝 → 409 tool.hook_denied,confirmation 仍 pending。"""

    def responder(body: dict[str, Any], index: int) -> dict[str, Any]:
        if index == 0:  # chat 阶段放行 → 落 pending
            return {"decision": "allow"}
        return {"decision": "deny", "reason": "确认后仍拒绝"}

    server = _HookServer(responder)
    seen: list[dict[str, Any]] = []
    replies = [_write_call(), "请确认后查看结果。"]
    with _hooks_client(
        tmp_path, server=server, replies=replies, write_seen=seen
    ) as client:
        _install_hook(client, "PreToolUse")
        conversation_id = _create_conversation(client)
        _, frame = _chat_stream(client, conversation_id)
        confirmation_id = frame["confirmation_id"]
        url = (
            f"/api/v1/conversations/{conversation_id}"
            f"/tool-confirmations/{confirmation_id}/confirm"
        )

        denied = client.post(url, headers=ACCOUNT)
        again = client.post(url, headers=ACCOUNT)

    assert denied.status_code == 409
    assert denied.json()["code"] == "tool.hook_denied"
    assert "确认后仍拒绝" in denied.json()["details"]["reason"]
    # 仍是 pending(不是 not_pending),第二次确认同样被拒 → 不会被误执行
    assert again.status_code == 409
    assert again.json()["code"] == "tool.hook_denied"
    assert seen == []


# ---------------------------------------------------------------- 压缩


def _messages(count: int) -> list[ChatMessage]:
    now = datetime(2026, 11, 1, 12, 0)
    return [
        ChatMessage(
            account_id="t-1",
            conversation_id="c-1",
            role="user",
            content=f"消息{index}",
            created_at=now + timedelta(seconds=index),
        )
        for index in range(count)
    ]


@asynccontextmanager
async def _db_session(tmp_path: Path, settings: Any) -> AsyncIterator[Any]:
    factory = create_session_factory(settings.database_url)
    try:
        async with factory.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_scope(factory) as session:
            yield session
    finally:
        await factory.engine.dispose()


async def _compact(
    tmp_path: Path, settings: Any, fake: FakeLLM
) -> tuple[Any, list[Any]]:
    async with _db_session(tmp_path, settings) as session:
        result = await compaction.compact_history(
            session,
            "t-1",
            "c-1",
            fake,
            rows=_messages(6),
            threshold_tokens=1,
            keep_recent=2,
            summary_max_chars=100,
        )
        return result, fake.calls


def _seed_hook(settings: Any, tmp_path: Path, event: str) -> None:
    async def seed() -> None:
        async with _db_session(tmp_path, settings) as session:
            await HookRepository(session).add(
                AccountHook(
                    account_id="t-1",
                    event=event,
                    matcher="*",
                    target=HOOK_TARGET,
                    secret="s3cr3t",
                    timeout_seconds=3,
                    enabled=True,
                )
            )

    asyncio.run(seed())


def test_pre_compact_skip_returns_none_and_skips_summarizer(tmp_path: Path) -> None:
    server = _HookServer(
        lambda body, index: {"decision": "skip", "reason": "本轮不压缩"}
    )
    settings = _settings(tmp_path, "compact-skip.db", hooks_enabled=True)
    hooks_service.configure_hooks(
        settings, transport=httpx.MockTransport(server), resolver=_resolver
    )
    try:
        _seed_hook(settings, tmp_path, "PreCompact")
        fake = FakeLLM(["不应被调用"])
        result, calls = asyncio.run(_compact(tmp_path, settings, fake))
    finally:
        hooks_service.reset_hooks()

    assert result is None
    assert calls == []
    assert server.bodies[0]["event"] == "PreCompact"
    assert server.bodies[0]["conversation_id"] == "c-1"
    assert server.bodies[0]["tokens_before"] > 1
    assert server.bodies[0]["reason"] == "threshold"


def test_pre_compact_allow_still_compacts(tmp_path: Path) -> None:
    server = _HookServer(lambda body, index: {"decision": "allow"})
    settings = _settings(tmp_path, "compact-allow.db", hooks_enabled=True)
    hooks_service.configure_hooks(
        settings, transport=httpx.MockTransport(server), resolver=_resolver
    )
    try:
        _seed_hook(settings, tmp_path, "PreCompact")
        fake = FakeLLM(["这是摘要"])
        result, calls = asyncio.run(_compact(tmp_path, settings, fake))
    finally:
        hooks_service.reset_hooks()

    assert result is not None
    assert result.summary == "这是摘要"
    assert len(calls) == 1
    assert server.bodies[0]["event"] == "PreCompact"


# ---------------------------------------------------------------- 会话事件


def test_session_start_and_end_fire_after_commit(tmp_path: Path) -> None:
    server = _HookServer(lambda body, index: {"decision": "allow"})
    with _hooks_client(tmp_path, server=server, replies=[]) as client:
        _install_hook(client, "SessionStart")
        _install_hook(client, "SessionEnd")
        conversation_id = _create_conversation(client)
        delete = client.delete(
            f"/api/v1/conversations/{conversation_id}", headers=ACCOUNT
        )

    assert delete.status_code == 204
    assert [body["event"] for body in server.bodies] == ["SessionStart", "SessionEnd"]
    assert server.bodies[0]["conversation_id"] == conversation_id
    assert server.bodies[1]["conversation_id"] == conversation_id


def test_session_hook_failure_does_not_break_conversation(tmp_path: Path) -> None:
    def responder(body: dict[str, Any], index: int) -> Any:
        return httpx.ConnectError("boom")

    server = _HookServer(responder)
    with _hooks_client(tmp_path, server=server, replies=[]) as client:
        _install_hook(client, "SessionStart")
        conversation_id = _create_conversation(client)

    assert conversation_id
    assert len(server.bodies) == 1
