"""chat 路径接外部工具(MCP):extras factory → 对话工具注册表(§6.8)。

对话没有审批流(§6.7),所以能不能在对话里执行由 ToolSpec.requires_confirmation
决定(MCP server 的 auto_approve 就在 tool_specs 里映射成它):

- requires_confirmation=False 的工具:直接执行并回喂结果;
- requires_confirmation=True 的工具:不执行,回喂错误引导改用「任务」;
- extras 工厂取失败:只降级外部工具,对话照常回答(与 run 路径同口径)。
"""

import json

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import ToolSpec, build_tools
from tests.conftest import TENANT, FakeConnector, FakeLLM, _settings, verifier


def _client(tmp_path, fake: FakeLLM, extras_factory) -> TestClient:
    connector = FakeConnector()
    app = create_app(
        _settings(tmp_path, "chat-extras.db", llm_enabled=True, tools_enabled=True),
        llm_client=fake,
        toolkit_factory=lambda tenant: build_tools(tenant, connector=connector),
        tool_extras_factory=extras_factory,
        token_verifier=verifier(),
    )
    return TestClient(app)


def _create_conversation(client: TestClient) -> str:
    resp = client.post("/api/v1/conversations", json={}, headers=TENANT)
    assert resp.status_code == 201
    return resp.json()["id"]


def _stream(client: TestClient, conversation_id: str, content: str, mode="chat"):
    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": content, "mode": mode},
        headers=TENANT,
    )
    events = []
    for frame in resp.text.split("\n\n"):
        name = data = None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if name and data:
            events.append((name, json.loads(data)))
    return resp, events


def _tool_call_reply(name: str, arguments: str) -> LLMReply:
    return LLMReply(
        content="",
        tool_calls=(ToolCall(id="call-1", name=name, arguments=arguments),),
    )


def _mcp_spec(handler, *, requires_confirmation: bool) -> ToolSpec:
    return ToolSpec(
        name="mcp__mock__echo",
        description="回显",
        parameters={"type": "object"},
        handler=handler,
        requires_confirmation=requires_confirmation,
    )


def test_chat_executes_auto_approved_mcp_tool(tmp_path) -> None:
    """auto_approve 的 MCP 工具(requires_confirmation=False):对话里直接执行。"""
    seen: list[dict] = []

    async def handler(arguments: dict) -> str:
        seen.append(arguments)
        return f"echo: {arguments.get('text')}"

    async def extras_factory(tenant_id: str) -> list[ToolSpec]:
        assert tenant_id == "t-1"
        return [_mcp_spec(handler, requires_confirmation=False)]

    fake = FakeLLM(
        [_tool_call_reply("mcp__mock__echo", '{"text": "hi"}'), "工具回了 hi。"]
    )
    with _client(tmp_path, fake, extras_factory) as client:
        conversation_id = _create_conversation(client)
        resp, events = _stream(client, conversation_id, "用 echo 工具回显 hi")

        assert resp.status_code == 200
        tool_events = [data for name, data in events if name == "tool"]
        assert tool_events == [
            {"name": "mcp__mock__echo", "arguments": '{"text": "hi"}', "ok": True}
        ]
        # 外部工具真的被执行了(结果回喂给第二轮模型调用)
        assert seen == [{"text": "hi"}]
        assert any(m.get("role") == "tool" for m in fake.calls[1])
        # 模型从对话工具列表里看得到 MCP 工具(命名空间隔离的名字)
        names = [tool["function"]["name"] for tool in fake.tools_seen[0]]
        assert "mcp__mock__echo" in names
        assert events[-1][1]["reply"]["content"] == "工具回了 hi。"


def test_chat_rejects_mcp_tool_needing_confirmation(tmp_path) -> None:
    """未开 auto_approve 的 MCP 工具:对话里不执行,提示改用「任务」。"""
    called: list[dict] = []

    async def handler(arguments: dict) -> str:
        called.append(arguments)
        return "不该被调用"

    async def extras_factory(tenant_id: str) -> list[ToolSpec]:
        return [_mcp_spec(handler, requires_confirmation=True)]

    fake = FakeLLM(
        [
            _tool_call_reply("mcp__mock__echo", '{"text": "hi"}'),
            "这个工具需要确认,请改用「任务」发送。",
        ]
    )
    with _client(tmp_path, fake, extras_factory) as client:
        conversation_id = _create_conversation(client)
        _, events = _stream(client, conversation_id, "回显 hi")

        tool_events = [data for name, data in events if name == "tool"]
        assert tool_events[0]["name"] == "mcp__mock__echo"
        assert tool_events[0]["ok"] is False
        assert called == []
        tool_messages = [m for m in fake.calls[1] if m.get("role") == "tool"]
        assert "「任务」" in tool_messages[0]["content"]


def test_chat_post_message_path_uses_extras(tmp_path) -> None:
    """非流式入口(一次拿全文)同样合并外部工具。"""

    async def handler(arguments: dict) -> str:
        return "echo: " + str(arguments.get("text"))

    async def extras_factory(tenant_id: str) -> list[ToolSpec]:
        return [_mcp_spec(handler, requires_confirmation=False)]

    fake = FakeLLM(
        [_tool_call_reply("mcp__mock__echo", '{"text": "hi"}'), "回显完成。"]
    )
    with _client(tmp_path, fake, extras_factory) as client:
        conversation_id = _create_conversation(client)
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "回显 hi", "mode": "chat"},
            headers=TENANT,
        )

        assert resp.status_code == 201
        result = resp.json()
        assert result["mode"] == "chat"
        assert result["reply"]["content"] == "回显完成。"


def test_chat_degrades_when_extras_factory_fails(tmp_path) -> None:
    """extras 取失败只降级外部工具:对话仍能回答,不报错。"""

    async def broken_extras(tenant_id: str) -> list[ToolSpec]:
        raise RuntimeError("MCP 注册表挂了")

    fake = FakeLLM(["你好,有什么可以帮你的?"])
    with _client(tmp_path, fake, broken_extras) as client:
        conversation_id = _create_conversation(client)
        resp, events = _stream(client, conversation_id, "你好")

        assert resp.status_code == 200
        names = [name for name, _ in events]
        assert "error" not in names
        deltas = "".join(data["text"] for name, data in events if name == "delta")
        assert deltas == "你好,有什么可以帮你的?"
