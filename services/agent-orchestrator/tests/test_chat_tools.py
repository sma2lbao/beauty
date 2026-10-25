"""对话内 function tools(§6.8):chat 直答路径的只读工具循环。

- 只读工具(search_knowledge 等)直接执行并回喂;
- 危险写操作(DANGER)不执行,回喂错误引导改用「任务」;
- 无工具时行为与原先一致(单轮直答)。
"""

import json
from collections.abc import Iterator

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import ToolSpec, build_tools
from tests.conftest import (
    TENANT,
    FakeDangerTool,
    FakeKnowledge,
    FakeLLM,
    _settings,
    verifier,
)


def _tool_client(
    tmp_path,
    fake: FakeLLM,
    knowledge: FakeKnowledge | None = None,
    *,
    extras: tuple[ToolSpec, ...] = (),
) -> Iterator[TestClient]:
    knowledge = knowledge or FakeKnowledge()
    app = create_app(
        _settings(tmp_path, "chat-tools.db", llm_enabled=True, tools_enabled=True),
        llm_client=fake,
        toolkit_factory=lambda tenant: build_tools(
            tenant, knowledge=knowledge
        ).extended(list(extras)),
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
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if name and data:
            events.append((name, json.loads(data)))
    return resp, events


def _tool_call_reply(name: str, arguments: str, call_id: str = "call-1") -> LLMReply:
    return LLMReply(
        content="",
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
    )


def test_chat_tool_loop_executes_readonly_tool(tmp_path) -> None:
    """只读工具:模型请求 search_knowledge → 执行 → 结果回喂 → 最终正文。"""
    knowledge = FakeKnowledge()
    fake = FakeLLM(
        [
            _tool_call_reply("search_knowledge", '{"query": "本周日程"}'),
            "本周日程在周四评审,共 1 条。",
        ]
    )
    for client in _tool_client(tmp_path, fake, knowledge):
        conversation_id = _create_conversation(client)

        resp, events = _stream(client, conversation_id, "我最近一条日程是什么?")

        assert resp.status_code == 200
        names = [name for name, _ in events]
        assert names[0] == "meta"
        assert "tool" in names and "delta" in names
        assert names[-1] == "done"

        tool_events = [data for name, data in events if name == "tool"]
        assert len(tool_events) == 1
        event = tool_events[0]
        # P1 审计字段:风险等级与 error_code 随事件下发,duration_ms 为实测整数
        assert event["name"] == "search_knowledge"
        assert event["arguments"] == '{"query": "本周日程"}'
        assert event["ok"] is True
        assert event["risk"] == "read"
        assert event["decision"] == "auto"
        assert event["result_chars"] > 0
        # 成功结果没有 error_code/truncated:SSE 帧省略键(旧客户端兼容)
        assert "error_code" not in event
        assert "truncated" not in event
        assert isinstance(event["duration_ms"], int)
        assert event["duration_ms"] >= 0
        assert knowledge.queries == [("t-1", "本周日程", 5)]
        # 工具结果回喂给了第二轮模型调用(tool role 消息)
        second_call = fake.calls[1]
        assert any(m.get("role") == "tool" for m in second_call)
        # 最终回复落库
        assert events[-1][1]["reply"]["content"] == "本周日程在周四评审,共 1 条。"


def test_chat_tool_loop_rejects_write_tool(tmp_path) -> None:
    """危险写操作:不执行,回喂错误;模型据此引导用户改用任务。"""
    danger = FakeDangerTool()
    fake = FakeLLM(
        [
            _tool_call_reply("external_publish", '{"title": "对外发布说明"}'),
            "对外发布请改用「任务」发送,任务路径有人工确认。",
        ]
    )
    for client in _tool_client(tmp_path, fake, extras=(danger.spec(),)):
        conversation_id = _create_conversation(client)

        _, events = _stream(client, conversation_id, "帮我对外发布说明")

        tool_events = [data for name, data in events if name == "tool"]
        assert tool_events[0]["name"] == "external_publish"
        assert tool_events[0]["ok"] is False
        assert tool_events[0]["risk"] == "danger"
        assert tool_events[0]["decision"] == "confirm"
        # 未执行:没有耗时,错误信封是旧的纯 error 文本
        assert tool_events[0]["duration_ms"] == 0
        assert tool_events[0]["error_code"] == "tool.error"
        # 危险写操作绝没有被执行
        assert danger.sent == []
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


def test_chat_tool_rounds_cap_forces_final_answer(tmp_path) -> None:
    """轮次达上限仍在要工具:补一次不带工具的收束调用,不把空正文当答案。"""
    knowledge = FakeKnowledge()
    # 每一轮都请求工具(不收敛),但带上正文
    replies = [
        LLMReply(
            content=f"第 {i} 轮小结",
            tool_calls=(
                ToolCall(
                    id=f"call-{i}",
                    name="search_knowledge",
                    arguments='{"query": "本周日程"}',
                ),
            ),
        )
        for i in range(1, 6)
    ]
    replies.append("收束:日程已读取,共 1 条")
    fake = FakeLLM(replies)
    for client in _tool_client(tmp_path, fake, knowledge):
        conversation_id = _create_conversation(client)

        _, events = _stream(client, conversation_id, "查日程")

        # 5 轮工具调用(上限)+ 1 次不带工具的收束调用
        assert len(fake.calls) == 6
        tool_events = [data for name, data in events if name == "tool"]
        assert len(tool_events) == 5
        # 收束那次不下发工具,逼模型用已有结果写结论
        assert fake.tools_seen[-1] is None
        deltas = "".join(data["text"] for name, data in events if name == "delta")
        assert deltas == "收束:日程已读取,共 1 条"
