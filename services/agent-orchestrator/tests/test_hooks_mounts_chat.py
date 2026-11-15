"""P1-B 对话执行点上的 hooks:PreToolUse 拒绝不落 pending、PostToolUse note 可见。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.conftest import ACCOUNT, FakeKnowledge, FakeLLM
from tests.support.hooks_mounts import (
    WRITE_TOOL,
    _chat_stream,
    _create_conversation,
    _hooks_client,
    _HookServer,
    _install_hook,
    _read_call,
    _write_call,
)

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
