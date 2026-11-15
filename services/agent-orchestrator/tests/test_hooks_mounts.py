"""P1-B 应用级 hooks 挂点:图内自动分支与人工确认后重放(docs/design/hooks.md §6/§10)。

钉住顺序约束:PreToolUse 先于审批策略(deny 不产生 pending)、PostToolUse 在
真正执行之后只向模型可见内容追加 note。对话执行点见 test_hooks_mounts_chat.py,
压缩见 test_hooks_mounts_compact.py,会话事件见 test_hooks_mounts_session.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.conftest import ACCOUNT, FakeDangerTool, FakeKnowledge, FakeLLM, _create_run
from tests.support.hooks_mounts import (
    VERIFY_PASS,
    _danger_call,
    _hooks_client,
    _HookServer,
    _install_hook,
    _read_call,
    _run_with_tool,
    _tool_calls,
    _tool_messages,
)

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
