"""对话内 function tools(§6.8):chat 直答路径的只读工具循环。

- 只读工具(fetch_emails/search_knowledge 等)直接执行并回喂;
- 写操作(send_email)不执行,回喂错误引导改用「任务」;
- 无工具时行为与原先一致(单轮直答)。
"""

import json

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import build_tools
from tests.conftest import TENANT, FakeConnector, FakeLLM, _settings, verifier


def _tool_client(tmp_path, fake: FakeLLM, connector: FakeConnector) -> TestClient:
    app = create_app(
        _settings(tmp_path, "chat-tools.db", llm_enabled=True, tools_enabled=True),
        llm_client=fake,
        toolkit_factory=lambda tenant: build_tools(tenant, connector=connector),
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        yield client


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
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if name and data:
            events.append((name, json.loads(data)))
    return resp, events


def _tool_call_reply(name: str, arguments: str, call_id: str = "call-1") -> LLMReply:
    return LLMReply(
        content="",
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
    )


def test_chat_tool_loop_executes_readonly_tool(tmp_path) -> None:
    """只读工具:模型请求 fetch_emails → 执行 → 结果回喂 → 最终正文。"""
    connector = FakeConnector()
    fake = FakeLLM(
        [
            _tool_call_reply("fetch_emails", '{"connection_id": "conn-1"}'),
            "最近一封是周报。",
        ]
    )
    for client in _tool_client(tmp_path, fake, connector):
        conversation_id = _create_conversation(client)

        resp, events = _stream(client, conversation_id, "我最近一封邮件是什么?")

        assert resp.status_code == 200
        names = [name for name, _ in events]
        assert names[0] == "meta"
        assert "tool" in names and "delta" in names
        assert names[-1] == "done"

        tool_events = [data for name, data in events if name == "tool"]
        assert tool_events == [
            {
                "name": "fetch_emails",
                "arguments": '{"connection_id": "conn-1"}',
                "ok": True,
            }
        ]
        assert ("fetch_emails", "t-1", "conn-1", 5) in connector.calls
        # 工具结果回喂给了第二轮模型调用(tool role 消息)
        second_call = fake.calls[1]
        assert any(m.get("role") == "tool" for m in second_call)
        # 最终回复落库
        assert events[-1][1]["reply"]["content"] == "最近一封是周报。"


def test_chat_tool_loop_rejects_write_tool(tmp_path) -> None:
    """写操作:不执行,回喂错误;模型据此引导用户改用任务。"""
    connector = FakeConnector()
    fake = FakeLLM(
        [
            _tool_call_reply(
                "send_email",
                '{"connection_id": "conn-1", "to": "a@b.c",'
                ' "subject": "s", "body": "b"}',
            ),
            "发邮件请改用「任务」发送,任务路径有人工确认。",
        ]
    )
    for client in _tool_client(tmp_path, fake, connector):
        conversation_id = _create_conversation(client)

        _, events = _stream(client, conversation_id, "帮我把报告发给老板")

        tool_events = [data for name, data in events if name == "tool"]
        assert tool_events[0]["name"] == "send_email"
        assert tool_events[0]["ok"] is False
        # 连接器的发信绝没有被调用
        assert connector.sent == []
        # 错误文本回喂给了模型
        second_call = fake.calls[1]
        tool_messages = [m for m in second_call if m.get("role") == "tool"]
        assert "「任务」" in tool_messages[0]["content"]


def test_chat_without_toolkit_is_plain_answer(tmp_path) -> None:
    """无工具(toolkit 为 None):走原单轮路径,不给模型传 tools。"""
    fake = FakeLLM(["你好!有什么可以帮你的?"])
    from tests.conftest import _client

    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)

        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "你好", "mode": "chat"},
            headers=TENANT,
        )

        assert resp.status_code == 201
        result = resp.json()
        assert result["mode"] == "chat"
        assert result["reply"]["content"] == "你好!有什么可以帮你的?"
        # 没传 tools 参数
        assert fake.tools_seen[0] is None


def test_chat_tool_rounds_cap_falls_back_to_last_text(tmp_path) -> None:
    """轮次达上限仍在要工具:用最后收到的正文兜底,不再调模型。"""
    connector = FakeConnector()
    # 每一轮都请求工具(不收敛),但带上正文
    replies = [
        LLMReply(
            content=f"第 {i} 轮小结",
            tool_calls=(ToolCall(id=f"call-{i}", name="fetch_emails",
                                 arguments='{"connection_id": "conn-1"}'),),
        )
        for i in range(1, 4)
    ]
    fake = FakeLLM(replies)
    for client in _tool_client(tmp_path, fake, connector):
        conversation_id = _create_conversation(client)

        _, events = _stream(client, conversation_id, "查邮件")

        # 模型只被调了 3 轮(上限),tool 事件 3 个,delta 拼出最后正文
        assert len(fake.calls) == 3
        tool_events = [data for name, data in events if name == "tool"]
        assert len(tool_events) == 3
        deltas = "".join(data["text"] for name, data in events if name == "delta")
        assert deltas == "第 3 轮小结"
