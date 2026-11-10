"""P0-4 审批策略引擎:纯函数求值 + 端点契约 + run/对话里的实际生效。"""

from __future__ import annotations

import asyncio

from database.session import create_session_factory, session_scope

from agent_orchestrator.repositories import ConversationApprovalGrantRepository
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services.approval import ApprovalAction, decide, match_rule
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import (
    ACCOUNT,
    OTHER,
    FakeDangerTool,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _settings,
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


# ---- 端点:整表替换 + 账号隔离 ----


def test_policies_roundtrip_and_validation(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []

        resp = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {"tool": "external_publish", "action": "auto"},
                    {"tool": "*", "action": "confirm"},
                ]
            },
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        assert [row["tool"] for row in resp.json()] == ["*", "external_publish"]

        # 整表替换:再次 PUT 只提交一条,旧规则消失
        resp = client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "search_knowledge", "action": "deny"}]},
            headers=ACCOUNT,
        )
        assert [row["tool"] for row in resp.json()] == ["search_knowledge"]

        bad = client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "x", "action": "explode"}]},
            headers=ACCOUNT,
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
            headers=ACCOUNT,
        )
        assert dup.status_code == 422


def test_policies_are_account_scoped(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "*", "action": "deny"}]},
            headers=ACCOUNT,
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
            headers=ACCOUNT,
        )
        rows = client.get("/api/v1/approval-policies", headers=ACCOUNT).json()
        assert rows == [
            {
                "tool": "a_tool",
                "action": "auto",
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
            },
            {
                "tool": "b_tool",
                "action": "deny",
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
            },
        ]
        client.put("/api/v1/approval-policies", json={"policies": []}, headers=ACCOUNT)
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []


# ---- 纯函数:参数级规则(§6.1) ----


def test_match_rule_matches_arguments_by_mode() -> None:
    policies = [
        {
            "tool": "deploy",
            "action": "auto",
            "arg_name": "env",
            "arg_match": "exact",
            "arg_value": "staging",
        },
        {
            "tool": "deploy",
            "action": "confirm",
            "arg_name": "env",
            "arg_match": "prefix",
            "arg_value": "prod",
        },
        {
            "tool": "deploy",
            "action": "deny",
            "arg_name": "path",
            "arg_match": "glob",
            "arg_value": "*.pem",
        },
    ]
    assert match_rule(policies, "deploy", {"env": "staging"})["action"] == "auto"
    assert match_rule(policies, "deploy", {"env": "production"})["action"] == "confirm"
    assert match_rule(policies, "deploy", {"path": "secret.pem"})["action"] == "deny"
    # 参数缺失 / 值不命中 → 未命中
    assert match_rule(policies, "deploy", {"env": "dev"}) is None
    assert match_rule(policies, "deploy", None) is None


def test_match_rule_parameter_rule_beats_tool_default() -> None:
    policies = [
        {"tool": "publish", "action": "deny"},
        {
            "tool": "publish",
            "action": "auto",
            "arg_name": "title",
            "arg_match": "prefix",
            "arg_value": "周报",
        },
    ]
    assert (
        match_rule(policies, "publish", {"title": "周报 2026-W45"})["action"] == "auto"
    )
    # 参数不命中:退到同工具的无条件规则
    assert match_rule(policies, "publish", {"title": "别的"})["action"] == "deny"
    assert decide(policies, "publish", ToolRisk.WRITE) is ApprovalAction.DENY


def test_match_rule_prefers_grant_then_severity() -> None:
    grant = {
        "tool": "notes__create",
        "action": "auto",
        "arg_name": "",
        "origin": "grant",
    }
    account_confirm = {
        "tool": "notes__create",
        "action": "confirm",
        "origin": "account",
    }
    # 同档内 grant 行优先于账号策略行
    assert match_rule([account_confirm, grant], "notes__create")["origin"] == "grant"
    assert (
        decide([account_confirm, grant], "notes__create", ToolRisk.WRITE)
        is ApprovalAction.AUTO
    )
    # 同档同来源:deny > confirm > auto
    rows = [
        {"tool": "t", "action": "auto", "origin": "account"},
        {"tool": "t", "action": "deny", "origin": "account"},
        {"tool": "t", "action": "confirm", "origin": "account"},
    ]
    assert decide(rows, "t", ToolRisk.READ) is ApprovalAction.DENY
    # 参数规则优先于无条件规则
    param = {
        "tool": "t",
        "action": "auto",
        "arg_name": "x",
        "arg_match": "exact",
        "arg_value": "1",
        "origin": "account",
    }
    assert decide([*rows, param], "t", ToolRisk.READ, {"x": "1"}) is ApprovalAction.AUTO


def test_match_rule_wildcard_and_legacy_rows() -> None:
    policies = [
        {"tool": "*", "action": "deny"},
        {
            "tool": "*",
            "action": "auto",
            "arg_name": "dry_run",
            "arg_match": "exact",
            "arg_value": "true",
        },
    ]
    assert match_rule(policies, "anything", {"dry_run": True})["action"] == "auto"
    assert match_rule(policies, "anything", {})["action"] == "deny"
    # 旧两键行(无参数列)继续生效,DANGER 默认值也能被覆盖
    assert (
        decide(
            [{"tool": "external_publish", "action": "auto"}],
            "external_publish",
            ToolRisk.DANGER,
        )
        is ApprovalAction.AUTO
    )
    assert (
        decide(
            [{"tool": "external_publish", "action": "deny"}],
            "external_publish",
            ToolRisk.DANGER,
        )
        is ApprovalAction.DENY
    )


def test_match_rule_ignores_broken_arguments_and_match_mode() -> None:
    # 非法 arg_match:该参数规则不匹配,退到无条件规则(不抛异常)
    policies = [
        {
            "tool": "t",
            "action": "deny",
            "arg_name": "x",
            "arg_match": "regex",
            "arg_value": "1",
        },
        {"tool": "t", "action": "auto"},
    ]
    assert match_rule(policies, "t", {"x": "1"})["action"] == "auto"
    # 坏 JSON 参数:视为空映射,带参数条件的规则一律不匹配
    only_param = [
        {
            "tool": "t",
            "action": "deny",
            "arg_name": "x",
            "arg_match": "exact",
            "arg_value": "1",
        }
    ]
    assert match_rule(only_param, "t", "{not json") is None
    # 非字符串参数值按 JSON 文本比较
    numeric = [
        {
            "tool": "t",
            "action": "auto",
            "arg_name": "n",
            "arg_match": "exact",
            "arg_value": "1",
        }
    ]
    assert match_rule(numeric, "t", {"n": 1})["action"] == "auto"


# ---- 端点:参数列往返 + 校验 ----


def test_policy_argument_fields_roundtrip_and_validation(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        resp = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {
                        "tool": "deploy",
                        "action": "auto",
                        "arg_name": "env",
                        "arg_match": "prefix",
                        "arg_value": "stag",
                    },
                    {"tool": "deploy", "action": "deny"},
                ]
            },
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        rows = client.get("/api/v1/approval-policies", headers=ACCOUNT).json()
        # 排序键 tool, arg_name:无条件行("")在参数行("env")之前
        assert rows == [
            {
                "tool": "deploy",
                "action": "deny",
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
            },
            {
                "tool": "deploy",
                "action": "auto",
                "arg_name": "env",
                "arg_match": "prefix",
                "arg_value": "stag",
            },
        ]

        invalid = [
            # arg_name 非空但缺 arg_match/arg_value
            [{"tool": "t", "action": "auto", "arg_name": "x"}],
            # 非法 arg_match
            [
                {
                    "tool": "t",
                    "action": "auto",
                    "arg_name": "x",
                    "arg_match": "regex",
                    "arg_value": "1",
                }
            ],
            # arg_name 非空但 arg_value 为空
            [
                {
                    "tool": "t",
                    "action": "auto",
                    "arg_name": "x",
                    "arg_match": "exact",
                    "arg_value": "",
                }
            ],
            # arg_name 为空却给了 arg_match(三列必须同空)
            [
                {
                    "tool": "t",
                    "action": "auto",
                    "arg_name": "",
                    "arg_match": "exact",
                    "arg_value": "1",
                }
            ],
        ]
        for policies in invalid:
            resp = client.put(
                "/api/v1/approval-policies",
                json={"policies": policies},
                headers=ACCOUNT,
            )
            assert resp.status_code == 422, policies

        # 唯一键 = (tool, arg_name, arg_match, arg_value):同工具不同参数条件可共存
        ok = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {
                        "tool": "t",
                        "action": "auto",
                        "arg_name": "env",
                        "arg_match": "exact",
                        "arg_value": "staging",
                    },
                    {
                        "tool": "t",
                        "action": "deny",
                        "arg_name": "env",
                        "arg_match": "exact",
                        "arg_value": "prod",
                    },
                ]
            },
            headers=ACCOUNT,
        )
        assert ok.status_code == 200
        dup = client.put(
            "/api/v1/approval-policies",
            json={
                "policies": [
                    {
                        "tool": "t",
                        "action": "auto",
                        "arg_name": "env",
                        "arg_match": "exact",
                        "arg_value": "staging",
                    },
                    {
                        "tool": "t",
                        "action": "deny",
                        "arg_name": "env",
                        "arg_match": "exact",
                        "arg_value": "staging",
                    },
                ]
            },
            headers=ACCOUNT,
        )
        assert dup.status_code == 422


# ---- 服务层:grant 合并 / 替换不动 grant / 授权落库 ----


def _db_url(tmp_path) -> str:
    return _settings(
        tmp_path, "tools.db", llm_enabled=True, tools_enabled=True
    ).database_url


def test_load_policies_merges_conversation_grants(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "zz_tool", "action": "deny"}]},
            headers=ACCOUNT,
        )
        url = _db_url(tmp_path)

        async def seeded() -> list[dict[str, str]]:
            factory = create_session_factory(url)
            try:
                async with session_scope(factory) as session:
                    grant_repo = ConversationApprovalGrantRepository(session)
                    effective = await approval_service.record_grant(
                        session,
                        account_id="t-1",
                        conversation_id=conversation_id,
                        tool="aa_tool",
                        grant="session",
                    )
                    assert effective == "session"
                    assert await grant_repo.list_for_conversation(
                        "t-1", conversation_id
                    )
                    return await approval_service.load_policies(
                        session, "t-1", conversation_id
                    )
            finally:
                await factory.engine.dispose()

        rows = asyncio.run(seeded())
        # 账号行在前(按工具排序)、grant 行在后
        assert [(row["tool"], row["origin"]) for row in rows] == [
            ("zz_tool", "account"),
            ("aa_tool", "grant"),
        ]
        assert rows[1]["action"] == "auto"

        # PUT 整表替换只动账号行:grant 不受影响
        client.put("/api/v1/approval-policies", json={"policies": []}, headers=ACCOUNT)

        async def after_replace() -> list[dict[str, str]]:
            factory = create_session_factory(url)
            try:
                async with session_scope(factory) as session:
                    return await approval_service.load_policies(
                        session, "t-1", conversation_id
                    )
            finally:
                await factory.engine.dispose()

        rows = asyncio.run(after_replace())
        assert [row["origin"] for row in rows] == ["grant"]


def test_record_grant_session_without_conversation_degrades(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])):
        url = _db_url(tmp_path)

        async def degrade() -> list[dict[str, str]]:
            factory = create_session_factory(url)
            try:
                async with session_scope(factory) as session:
                    effective = await approval_service.record_grant(
                        session,
                        account_id="t-1",
                        conversation_id=None,
                        tool="notes__create",
                        grant="session",
                    )
                    assert effective == "once"
                    return await approval_service.load_policies(session, "t-1")
            finally:
                await factory.engine.dispose()

        assert asyncio.run(degrade()) == []


def test_record_grant_always_writes_account_auto_policy(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        url = _db_url(tmp_path)

        async def record(grant: str) -> str:
            factory = create_session_factory(url)
            try:
                async with session_scope(factory) as session:
                    return await approval_service.record_grant(
                        session,
                        account_id="t-1",
                        conversation_id=None,
                        tool="external_publish",
                        grant=grant,
                    )
            finally:
                await factory.engine.dispose()

        assert asyncio.run(record("once")) == "once"
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []
        assert asyncio.run(record("always")) == "always"
        rows = client.get("/api/v1/approval-policies", headers=ACCOUNT).json()
        assert rows == [
            {
                "tool": "external_publish",
                "action": "auto",
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
            }
        ]


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
