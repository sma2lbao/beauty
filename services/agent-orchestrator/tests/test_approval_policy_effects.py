"""P0-4 审批策略在 run/对话里的实际生效(含参数级规则只放行命中的那次调用)。"""

from __future__ import annotations

from agent_orchestrator.services.llm import LLMReply, ToolCall
from tests.conftest import (
    ACCOUNT,
    FakeDangerTool,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _tool_client,
)

VERIFY_PASS = '{"summary": "已完成", "verdicts": {"日程已整理": true}}'

# ---- 生效:auto 信任危险工具 / deny 拦截写工具 ----


def _send_danger() -> LLMReply:
    return LLMReply(
        content="",
        tool_calls=(
            ToolCall(id="call_w1", name="external_publish", arguments='{"title": "x"}'),
        ),
    )


def test_auto_policy_skips_confirmation_for_dangerous_tool(tmp_path) -> None:
    fake = FakeLLM(["# 计划", _send_danger(), "已发布", VERIFY_PASS, "# 交付物"])
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, extras=(danger.spec(),)) as client:
        client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "external_publish", "action": "auto"}]},
            headers=ACCOUNT,
        )
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()

    # 策略放行后不再停在确认闸,写操作真的执行了
    assert run["status"] == "completed"
    assert danger.sent == [{"title": "x"}]
    types = [
        e["type"]
        for e in client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()
    ]
    assert "run.confirmation.required.v1" not in types


def test_deny_policy_blocks_tool_and_feeds_error_back(tmp_path) -> None:
    fake = FakeLLM(
        [
            "# 计划",
            LLMReply(
                content="",
                tool_calls=(
                    ToolCall(
                        id="call_r1",
                        name="search_knowledge",
                        arguments='{"query": "日程"}',
                    ),
                ),
            ),
            "检索被禁止,改用已有信息汇总",
            VERIFY_PASS,
            "# 交付物",
        ]
    )
    knowledge = FakeKnowledge()
    with _tool_client(tmp_path, fake, knowledge) as client:
        client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "search_knowledge", "action": "deny"}]},
            headers=ACCOUNT,
        )
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()

    assert run["status"] == "completed"
    called = [e["payload"] for e in events if e["type"] == "run.tool.called.v1"]
    assert called[0]["name"] == "search_knowledge"
    assert called[0]["ok"] is False
    assert "审批策略" in called[0]["detail"]


def test_chat_respects_deny_policy(tmp_path) -> None:
    """对话里的写工具被 denyl 后不执行,模型改走别的说法。"""
    fake = FakeLLM(
        [
            LLMReply(
                content="",
                tool_calls=(
                    ToolCall(
                        id="call_r1",
                        name="search_knowledge",
                        arguments='{"query": "日程"}',
                    ),
                ),
            ),
            "没有检索权限,直接回答",
        ]
    )
    with _tool_client(tmp_path, fake) as client:
        client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "search_knowledge", "action": "deny"}]},
            headers=ACCOUNT,
        )
        conv = client.post(
            "/api/v1/conversations", json={"title": "闲聊"}, headers=ACCOUNT
        ).json()
        resp = client.post(
            f"/api/v1/conversations/{conv['id']}/messages",
            json={"content": "帮我看看日程", "mode": "chat"},
            headers=ACCOUNT,
        )
    assert resp.status_code == 201
    assert resp.json()["reply"]["content"] == "没有检索权限,直接回答"


# ---- 生效:参数级 auto 只放行参数命中的那次调用 ----


def test_argument_rule_auto_lets_matching_danger_call_through(tmp_path) -> None:
    fake = FakeLLM(["# 计划", _send_danger(), "已发布", VERIFY_PASS, "# 交付物"])
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, extras=(danger.spec(),)) as client:
        client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {
                        "tool": "external_publish",
                        "action": "auto",
                        "arg_name": "title",
                        "arg_match": "prefix",
                        "arg_value": "x",
                    }
                ]
            },
            headers=ACCOUNT,
        )
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()

    assert run["status"] == "completed"
    assert danger.sent == [{"title": "x"}]


def test_argument_rule_does_not_apply_to_other_arguments(tmp_path) -> None:
    fake = FakeLLM(["# 计划", _send_danger(), VERIFY_PASS, "# 交付物"])
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, extras=(danger.spec(),)) as client:
        client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {
                        "tool": "external_publish",
                        "action": "auto",
                        "arg_name": "title",
                        "arg_match": "prefix",
                        "arg_value": "对外",
                    }
                ]
            },
            headers=ACCOUNT,
        )
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
        # 参数不命中 → 仍是风险默认值,停在确认闸
        assert run["status"] == "awaiting_confirmation"
        client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "reject", "reason": "先别发"},
            headers=ACCOUNT,
        )

    assert danger.sent == []
