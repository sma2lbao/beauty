"""对话内写操作确认链路(§6.7 就地确认):pending 落库 → 确认执行 / 取消 / 过期。

覆盖契约(与前端已约定,字段名不得改):

- chat 里 risk=write 的调用不执行,工具帧带 confirmation_id / expires_at、
  decision="confirm"、error_code="tool.confirmation_required"、ok=false;
- 确认端点以 approved=True 重放同一 tool_call,结果落 assistant 消息并回填;
- 重复确认 / 已取消 → 409 tool_confirmation.not_pending(不重复执行);
- 过期 → 409 tool_confirmation.expired;不存在 / 跨账号 → 404。
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.repositories import (
    ChatToolConfirmationRepository,
    ConversationApprovalGrantRepository,
)
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import ToolRegistry, ToolRisk, ToolSpec
from tests.conftest import ACCOUNT, OTHER, FakeLLM, _settings, verifier

ACCOUNT_ID = "t-1"
WRITE_TOOL = "notes__create"
SECOND_TOOL = "notes__archive"


def _write_spec(name: str, seen: list[dict]) -> ToolSpec:
    async def handler(arguments: dict) -> str:
        seen.append(arguments)
        return json.dumps(
            {"created": True, "count": len(seen), "title": arguments.get("title")},
            ensure_ascii=False,
        )

    return ToolSpec(
        name=name,
        description="写笔记",
        parameters={"type": "object"},
        handler=handler,
        risk=ToolRisk.WRITE,
    )


def _tool_call_reply(name: str, arguments: str = '{"title": "周报"}') -> LLMReply:
    return LLMReply(
        content="",
        tool_calls=(ToolCall(id="call-1", name=name, arguments=arguments),),
    )


def _app(tmp_path: Path, seen: list[dict], replies: list) -> TestClient:
    settings = _settings(
        tmp_path, "tool-confirmations.db", llm_enabled=True, tools_enabled=True
    )
    registry = ToolRegistry([_write_spec(WRITE_TOOL, seen)])
    app = create_app(
        settings,
        llm_client=FakeLLM(replies),
        toolkit_factory=lambda account: registry,
        token_verifier=verifier(),
    )
    return TestClient(app)


def _create_conversation(client: TestClient) -> str:
    resp = client.post("/api/v1/conversations", json={}, headers=ACCOUNT)
    assert resp.status_code == 201
    return resp.json()["id"]


def _sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
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


def _stream_write_call(
    client: TestClient, conversation_id: str
) -> tuple[list[tuple[str, dict]], dict]:
    """发一条会触发 WRITE 调用的流式消息,返回 (事件, tool 帧)。"""
    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "把周报写进笔记", "mode": "chat"},
        headers=ACCOUNT,
    )
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    tool_events = [data for name, data in events if name == "tool"]
    assert len(tool_events) == 1
    return events, tool_events[0]


def _confirm_url(conversation_id: str, confirmation_id: str) -> str:
    return (
        f"/api/v1/conversations/{conversation_id}"
        f"/tool-confirmations/{confirmation_id}/confirm"
    )


def _cancel_url(conversation_id: str, confirmation_id: str) -> str:
    return (
        f"/api/v1/conversations/{conversation_id}"
        f"/tool-confirmations/{confirmation_id}/cancel"
    )


def _messages(client: TestClient, conversation_id: str) -> list[dict]:
    resp = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=ACCOUNT
    )
    assert resp.status_code == 200
    return resp.json()


def test_chat_write_call_creates_pending_confirmation(tmp_path: Path) -> None:
    """WRITE 不执行:工具帧带确认字段,TTL 10 分钟,回喂"请用户确认"。"""
    seen: list[dict] = []
    fake = FakeLLM(
        [_tool_call_reply(WRITE_TOOL), "已经生成确认请求,请在卡片上点击确认。"]
    )
    settings = _settings(
        tmp_path, "tool-confirmations.db", llm_enabled=True, tools_enabled=True
    )
    registry = ToolRegistry([_write_spec(WRITE_TOOL, seen)])
    app = create_app(
        settings,
        llm_client=fake,
        toolkit_factory=lambda account: registry,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        conversation_id = _create_conversation(client)
        events, frame = _stream_write_call(client, conversation_id)

        assert frame["name"] == WRITE_TOOL
        assert frame["arguments"] == '{"title": "周报"}'
        assert frame["ok"] is False
        assert frame["risk"] == "write"
        assert frame["decision"] == "confirm"
        assert frame["error_code"] == "tool.confirmation_required"
        assert frame["duration_ms"] == 0
        assert frame["confirmation_id"]
        expires_at = datetime.fromisoformat(frame["expires_at"])
        remaining = expires_at - datetime.now(UTC)
        assert timedelta(minutes=9) < remaining <= timedelta(minutes=10, seconds=30)

        # 工具没有被执行;回喂给模型的是"请用户确认"
        assert seen == []
        tool_messages = [m for m in fake.calls[1] if m.get("role") == "tool"]
        assert "确认" in tool_messages[0]["content"]

        # 模型正文照常下发并落 assistant 消息(不含执行结果)
        assert events[-1][0] == "done"
        assert events[-1][1]["reply"]["content"] == (
            "已经生成确认请求,请在卡片上点击确认。"
        )


def test_confirm_executes_once_and_persists_assistant_message(tmp_path: Path) -> None:
    """确认 → approved=True 执行一次、结果落 assistant 消息、行回填;重复确认 409。"""
    seen: list[dict] = []
    replies = [_tool_call_reply(WRITE_TOOL), "请确认后查看结果。"]
    with _app(tmp_path, seen, replies) as client:
        conversation_id = _create_conversation(client)
        _, frame = _stream_write_call(client, conversation_id)
        confirmation_id = frame["confirmation_id"]

        resp = client.post(
            _confirm_url(conversation_id, confirmation_id), headers=ACCOUNT
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["confirmation_id"] == confirmation_id
        assert result["status"] == "confirmed"
        assert result["ok"] is True
        assert result["error_code"] is None
        assert json.loads(result["result_text"])["created"] is True
        assert seen == [{"title": "周报"}]

        # 结果落成 assistant 消息(前端据此把结果插入对话)
        messages = _messages(client, conversation_id)
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["id"] == result["message_id"]
        assert messages[-1]["content"] == result["result_text"]

        # 重复确认:409 且不重复执行
        again = client.post(
            _confirm_url(conversation_id, confirmation_id), headers=ACCOUNT
        )
        assert again.status_code == 409
        assert again.json()["code"] == "tool_confirmation.not_pending"
        assert seen == [{"title": "周报"}]


def test_cancel_marks_cancelled_without_executing(tmp_path: Path) -> None:
    seen: list[dict] = []
    replies = [_tool_call_reply(WRITE_TOOL), "已取消。"]
    with _app(tmp_path, seen, replies) as client:
        conversation_id = _create_conversation(client)
        _, frame = _stream_write_call(client, conversation_id)
        confirmation_id = frame["confirmation_id"]

        resp = client.post(
            _cancel_url(conversation_id, confirmation_id), headers=ACCOUNT
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "confirmation_id": confirmation_id,
            "status": "cancelled",
        }
        assert seen == []

        confirm = client.post(
            _confirm_url(conversation_id, confirmation_id), headers=ACCOUNT
        )
        assert confirm.status_code == 409
        assert confirm.json()["code"] == "tool_confirmation.not_pending"
        assert seen == []


def test_confirmation_not_found(tmp_path: Path) -> None:
    seen: list[dict] = []
    replies = [_tool_call_reply(WRITE_TOOL), "请确认。"]
    with _app(tmp_path, seen, replies) as client:
        conversation_id = _create_conversation(client)
        resp = client.post(_confirm_url(conversation_id, "missing"), headers=ACCOUNT)
        assert resp.status_code == 404
        assert resp.json()["code"] == "tool_confirmation.not_found"


def test_confirmation_is_scoped_to_conversation_and_account(tmp_path: Path) -> None:
    """同一账号其它会话 / 其它账号都看不到这条确认(404)。"""
    seen: list[dict] = []
    replies = [_tool_call_reply(WRITE_TOOL), "请确认。"]
    with _app(tmp_path, seen, replies) as client:
        conversation_id = _create_conversation(client)
        _, frame = _stream_write_call(client, conversation_id)
        confirmation_id = frame["confirmation_id"]

        other_conversation = _create_conversation(client)
        resp = client.post(
            _confirm_url(other_conversation, confirmation_id), headers=ACCOUNT
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "tool_confirmation.not_found"

        # 跨账号:会话本身按既有口径不可见(conversation.not_found,404)
        cross = client.post(
            _confirm_url(conversation_id, confirmation_id), headers=OTHER
        )
        assert cross.status_code == 404
        assert cross.json()["code"] == "conversation.not_found"
        assert seen == []


def test_expired_confirmation_returns_409(tmp_path: Path) -> None:
    seen: list[dict] = []
    replies = [_tool_call_reply(WRITE_TOOL), "请确认。"]
    settings = _settings(
        tmp_path, "tool-confirmations.db", llm_enabled=True, tools_enabled=True
    )
    registry = ToolRegistry([_write_spec(WRITE_TOOL, seen)])
    app = create_app(
        settings,
        llm_client=FakeLLM(replies),
        toolkit_factory=lambda account: registry,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        conversation_id = _create_conversation(client)
        _, frame = _stream_write_call(client, conversation_id)
        confirmation_id = frame["confirmation_id"]

        async def expire() -> None:
            factory = create_session_factory(settings.database_url)
            try:
                async with session_scope(factory) as session:
                    repository = ChatToolConfirmationRepository(session)
                    row = await repository.get_for_conversation(
                        ACCOUNT_ID, conversation_id, confirmation_id
                    )
                    assert row is not None
                    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            finally:
                await factory.engine.dispose()

        asyncio.run(expire())

        resp = client.post(
            _confirm_url(conversation_id, confirmation_id), headers=ACCOUNT
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "tool_confirmation.expired"
        assert seen == []


def test_post_message_path_also_creates_pending(tmp_path: Path) -> None:
    """非流式入口走同一套确认链路:回复照常,确认端点可执行。"""
    seen: list[dict] = []
    replies = [_tool_call_reply(WRITE_TOOL), "已生成确认请求。"]
    with _app(tmp_path, seen, replies) as client:
        conversation_id = _create_conversation(client)
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "把周报写进笔记", "mode": "chat"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 201
        assert resp.json()["reply"]["content"] == "已生成确认请求。"
        assert seen == []

        # 非流式路径没有 tool 帧可拿,直接从库里取 pending 行(用独立短事务读)
        async def pending_id() -> str:
            factory = create_session_factory(
                _settings(
                    tmp_path,
                    "tool-confirmations.db",
                    llm_enabled=True,
                    tools_enabled=True,
                ).database_url
            )
            try:
                async with session_scope(factory) as session:
                    row = await ChatToolConfirmationRepository(
                        session
                    ).get_by_tool_call(ACCOUNT_ID, "call-1")
                    assert row is not None
                    assert row.conversation_id == conversation_id
                    return row.id
            finally:
                await factory.engine.dispose()

        confirmation_id = asyncio.run(pending_id())

        confirm = client.post(
            _confirm_url(conversation_id, confirmation_id), headers=ACCOUNT
        )
        assert confirm.status_code == 200
        assert confirm.json()["ok"] is True
        assert seen == [{"title": "周报"}]


def _db_url(tmp_path: Path) -> str:
    return _settings(
        tmp_path, "tool-confirmations.db", llm_enabled=True, tools_enabled=True
    ).database_url


def _grants(tmp_path: Path, conversation_id: str) -> list[str]:
    """直读会话授权表,返回工具名(按工具名排序)。"""

    async def read() -> list[str]:
        factory = create_session_factory(_db_url(tmp_path))
        try:
            async with session_scope(factory) as session:
                rows = await ConversationApprovalGrantRepository(
                    session
                ).list_for_conversation(ACCOUNT_ID, conversation_id)
                return [row.tool for row in rows]
        finally:
            await factory.engine.dispose()

    return asyncio.run(read())


def test_confirm_with_session_grant_auto_executes_next_call(tmp_path: Path) -> None:
    """session 授权:同会话同名工具下一轮直接执行(decision=auto,不再出卡片)。"""
    seen: list[dict] = []
    replies = [
        _tool_call_reply(WRITE_TOOL),
        "请确认。",
        _tool_call_reply(WRITE_TOOL),
        "已直接写入。",
    ]
    with _app(tmp_path, seen, replies) as client:
        conversation_id = _create_conversation(client)
        _, frame = _stream_write_call(client, conversation_id)

        resp = client.post(
            _confirm_url(conversation_id, frame["confirmation_id"]),
            json={"grant": "session"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert seen == [{"title": "周报"}]
        # 授权已落库(同会话 + 同名工具各一行)
        assert _grants(tmp_path, conversation_id) == [WRITE_TOOL]

        # 第二轮:同一工具命中 session 授权 → 直接执行,帧里 decision=auto
        second = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"content": "再写一份", "mode": "chat"},
            headers=ACCOUNT,
        )
        assert second.status_code == 200
        tool_events = [
            data for name, data in _sse_events(second.text) if name == "tool"
        ]
        assert len(tool_events) == 1
        assert tool_events[0]["decision"] == "auto"
        assert tool_events[0]["ok"] is True
        assert "confirmation_id" not in tool_events[0]
        assert seen == [{"title": "周报"}, {"title": "周报"}]


def test_confirm_with_always_grant_writes_account_policy(tmp_path: Path) -> None:
    """always 授权:写一条账号级 auto 策略,该工具以后不再确认。"""
    seen: list[dict] = []
    replies = [_tool_call_reply(WRITE_TOOL), "请确认。"]
    with _app(tmp_path, seen, replies) as client:
        conversation_id = _create_conversation(client)
        _, frame = _stream_write_call(client, conversation_id)
        resp = client.post(
            _confirm_url(conversation_id, frame["confirmation_id"]),
            json={"grant": "always"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        assert seen == [{"title": "周报"}]
        rows = client.get("/api/v1/approval-policies", headers=ACCOUNT).json()
        assert rows == [
            {
                "tool": WRITE_TOOL,
                "action": "auto",
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
            }
        ]
        # always 不写会话授权表
        assert _grants(tmp_path, conversation_id) == []


def test_confirm_without_grant_does_not_persist_authorization(tmp_path: Path) -> None:
    """不传 grant 等价 once:既没有会话授权,也没有账号策略。"""
    seen: list[dict] = []
    replies = [_tool_call_reply(WRITE_TOOL), "请确认。"]
    with _app(tmp_path, seen, replies) as client:
        conversation_id = _create_conversation(client)
        _, frame = _stream_write_call(client, conversation_id)
        resp = client.post(
            _confirm_url(conversation_id, frame["confirmation_id"]), headers=ACCOUNT
        )
        assert resp.status_code == 200
        assert seen == [{"title": "周报"}]
        assert _grants(tmp_path, conversation_id) == []
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []


def test_confirm_rejects_invalid_grant(tmp_path: Path) -> None:
    """非法 grant → 422,且不执行工具。"""
    seen: list[dict] = []
    replies = [_tool_call_reply(WRITE_TOOL), "请确认。"]
    with _app(tmp_path, seen, replies) as client:
        conversation_id = _create_conversation(client)
        _, frame = _stream_write_call(client, conversation_id)
        resp = client.post(
            _confirm_url(conversation_id, frame["confirmation_id"]),
            json={"grant": "forever"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 422
        assert seen == []
