"""P0-4 审批策略引擎:纯函数求值 + 端点契约 + run/对话里的实际生效。"""

from __future__ import annotations

from agent_orchestrator.services.approval import ApprovalAction, decide
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import (
    OTHER,
    TENANT,
    FakeDangerTool,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _tool_client,
)

VERIFY_PASS = '{"summary": "已完成", "verdicts": {"日程已整理": true}}'


# ---- 纯函数:策略求值 ----


def test_defaults_follow_risk_when_no_policy() -> None:
    assert decide([], "read_pdf", ToolRisk.READ) is ApprovalAction.AUTO
    assert decide([], "workspace_write_file", ToolRisk.WRITE) is ApprovalAction.AUTO
    assert decide([], "external_publish", ToolRisk.DANGER) is ApprovalAction.CONFIRM
    assert decide(None, "external_publish", ToolRisk.DANGER) is ApprovalAction.CONFIRM


def test_exact_tool_policy_overrides_default() -> None:
    policies = [
        {"tool": "external_publish", "action": "auto"},
        {"tool": "workspace_write_file", "action": "confirm"},
    ]
    assert decide(policies, "external_publish", ToolRisk.DANGER) is ApprovalAction.AUTO
    assert (
        decide(policies, "workspace_write_file", ToolRisk.WRITE)
        is ApprovalAction.CONFIRM
    )
    # 未命中的工具仍走风险默认值
    assert decide(policies, "read_pdf", ToolRisk.READ) is ApprovalAction.AUTO


def test_wildcard_applies_when_no_exact_match() -> None:
    policies = [
        {"tool": "*", "action": "deny"},
        {"tool": "read_pdf", "action": "auto"},
    ]
    assert decide(policies, "read_pdf", ToolRisk.READ) is ApprovalAction.AUTO
    assert decide(policies, "search_knowledge", ToolRisk.READ) is ApprovalAction.DENY
    # 精确规则优先于通配,即使风险更高
    assert decide(policies, "external_publish", ToolRisk.DANGER) is ApprovalAction.DENY


def test_unknown_action_falls_back_to_risk_default() -> None:
    assert (
        decide(
            [{"tool": "external_publish", "action": "maybe"}],
            "external_publish",
            ToolRisk.DANGER,
        )
        is ApprovalAction.CONFIRM
    )


# ---- 端点:整表替换 + 租户隔离 ----


def test_policies_roundtrip_and_validation(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        assert client.get("/api/v1/approval-policies", headers=TENANT).json() == []

        resp = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {"tool": "external_publish", "action": "auto"},
                    {"tool": "*", "action": "confirm"},
                ]
            },
            headers=TENANT,
        )
        assert resp.status_code == 200
        assert [row["tool"] for row in resp.json()] == ["*", "external_publish"]

        # 整表替换:再次 PUT 只提交一条,旧规则消失
        resp = client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "search_knowledge", "action": "deny"}]},
            headers=TENANT,
        )
        assert [row["tool"] for row in resp.json()] == ["search_knowledge"]

        bad = client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "x", "action": "explode"}]},
            headers=TENANT,
        )
        assert bad.status_code == 422
        dup = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {"tool": "x", "action": "auto"},
                    {"tool": "x", "action": "deny"},
                ]
            },
            headers=TENANT,
        )
        assert dup.status_code == 422


def test_policies_are_tenant_scoped(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "*", "action": "deny"}]},
            headers=TENANT,
        )
        assert client.get("/api/v1/approval-policies", headers=OTHER).json() == []


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
            headers=TENANT,
        )
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()

    # 策略放行后不再停在确认闸,写操作真的执行了
    assert run["status"] == "completed"
    assert danger.sent == [{"title": "x"}]
    types = [
        e["type"]
        for e in client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
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
            headers=TENANT,
        )
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()

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
            headers=TENANT,
        )
        conv = client.post(
            "/api/v1/conversations", json={"title": "闲聊"}, headers=TENANT
        ).json()
        resp = client.post(
            f"/api/v1/conversations/{conv['id']}/messages",
            json={"content": "帮我看看日程", "mode": "chat"},
            headers=TENANT,
        )
    assert resp.status_code == 201
    assert resp.json()["reply"]["content"] == "没有检索权限,直接回答"


def test_load_and_replace_policies(tmp_path) -> None:
    """服务层往返:替换后按工具名排序返回,空提交即清空。"""
    with _tool_client(tmp_path, FakeLLM([])) as client:
        client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {"tool": "b_tool", "action": "deny"},
                    {"tool": "a_tool", "action": "auto"},
                ]
            },
            headers=TENANT,
        )
        rows = client.get("/api/v1/approval-policies", headers=TENANT).json()
        assert rows == [
            {"tool": "a_tool", "action": "auto"},
            {"tool": "b_tool", "action": "deny"},
        ]
        client.put("/api/v1/approval-policies", json={"policies": []}, headers=TENANT)
        assert client.get("/api/v1/approval-policies", headers=TENANT).json() == []
