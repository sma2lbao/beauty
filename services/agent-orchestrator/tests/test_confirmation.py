"""写操作确认闸(§6.2):写工具先中断等人工放行,拒绝则不执行、如实记录。"""

import asyncio

from database.session import create_session_factory, session_scope
from sqlalchemy import select

from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import ConversationApprovalGrantRepository
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.run_execution_approvals import _apply_grants
from tests.conftest import (
    ACCOUNT,
    FakeDangerTool,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _settings,
    _tool_client,
)

DANGER_ARGS = '{"title": "对外发布说明"}'


def _send_call() -> LLMReply:
    """一次请求两个工具:只读检索(先执行)+ 危险写操作(待确认)。"""
    return LLMReply(
        content="",
        tool_calls=(
            ToolCall(
                id="call_read",
                name="search_knowledge",
                arguments='{"query": "本周日程"}',
            ),
            ToolCall(id="call_w1", name="external_publish", arguments=DANGER_ARGS),
        ),
    )


def test_write_tool_waits_for_confirmation_then_executes(tmp_path) -> None:
    verify_pass = '{"summary": "已发布", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(
        [
            "# 计划",
            _send_call(),  # execute:请求危险写操作 → 中断
            "已发布对外发布说明",  # 放行后 execute 收尾
            verify_pass,
            "# 交付物",
        ]
    )
    knowledge = FakeKnowledge()
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, knowledge, extras=(danger.spec(),)) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)

        # 1) 停在等待确认;写操作绝不能已经执行
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
        assert run["status"] == "awaiting_confirmation"
        assert danger.sent == []

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()
        required = next(
            e for e in events if e["type"] == "run.confirmation.required.v1"
        )
        assert required["payload"]["pending_writes"][0]["name"] == "external_publish"

        # 2) 放行 → 执行写操作 → 继续到 completed
        run = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve"},
            headers=ACCOUNT,
        ).json()
        assert run["status"] == "completed"
        assert danger.sent == [{"title": "对外发布说明"}]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()
        types = [e["type"] for e in events]
        assert "run.confirmation.received.v1" in types
        # 工具事件不重复:只读调用一次 + 写操作一次(§6.3 add reducer 增量落库)
        names = [
            e["payload"]["name"] for e in events if e["type"] == "run.tool.called.v1"
        ]
        assert names == ["search_knowledge", "external_publish"]
        # 顺序语义:只读在闸门之前,写操作在人工确认之后
        read_index = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "run.tool.called.v1"
            and e["payload"]["name"] == "search_knowledge"
        )
        write_index = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "run.tool.called.v1"
            and e["payload"]["name"] == "external_publish"
        )
        assert read_index < types.index("run.confirmation.required.v1")
        assert write_index > types.index("run.confirmation.received.v1")
        assert write_index < types.index("run.completed.v1")
        assert knowledge.queries == [("t-1", "本周日程", 5)]

        # P1 审计:人工确认后执行的写操作记为 approved,并带调用 id/风险/耗时
        write_payload = events[write_index]["payload"]
        assert write_payload["tool_call_id"] == "call_w1"
        assert write_payload["risk"] == "danger"
        assert write_payload["decision"] == "approved"
        assert write_payload["error_code"] is None
        assert isinstance(write_payload["duration_ms"], int)
        assert write_payload["duration_ms"] >= 0
        assert write_payload["result_chars"] > 0
        assert write_payload["truncated"] is False
        assert write_payload["paths"] == []
        assert write_payload["sha256_before"] is None
        assert write_payload["sha256_after"] is None
        # 只读工具走自动放行分支
        assert events[read_index]["payload"]["decision"] == "auto"
        assert events[read_index]["payload"]["tool_call_id"] == "call_read"


def test_write_tool_rejected_skips_execution(tmp_path) -> None:
    """拒绝写操作 → 不执行;拒绝原因进 results 并由交付物如实呈现。"""
    verify_pass = '{"summary": "未发布", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(["# 计划", _send_call(), verify_pass, "# 交付物"])
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, extras=(danger.spec(),)) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
        assert run["status"] == "awaiting_confirmation"

        run = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "reject", "reason": "先别发"},
            headers=ACCOUNT,
        ).json()
        assert run["status"] == "completed"
        assert danger.sent == []  # 关键:拒绝即不执行

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()
        results = next(e for e in events if e["type"] == "run.completed.v1")["payload"][
            "results"
        ]
        assert any("危险操作被拒绝:先别发" in item for item in results)
        # 只读工具执行了;写操作一次都没执行
        names = [
            e["payload"]["name"] for e in events if e["type"] == "run.tool.called.v1"
        ]
        assert names == ["search_knowledge"]
        send_events = [
            e
            for e in events
            if e["type"] == "run.tool.called.v1"
            and e["payload"]["name"] == "external_publish"
        ]
        assert send_events == []


def test_confirm_guard_requires_awaiting_state(tmp_path) -> None:
    """非 awaiting_confirmation 状态确认 → 409(状态守卫)。"""
    fake = FakeLLM(
        [
            "# 计划",
            "执行",
            '{"summary": "ok", "verdicts": {"日程已整理": true}}',
            "# 交付物",
        ]
    )
    with _tool_client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        # 尚未 approve:仍处于 awaiting_approval
        resp = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "run.invalid_state"


def test_confirmation_cycles_are_capped(tmp_path) -> None:
    """连续重复请求写操作:确认 ≤3 轮,之后如实告知未执行(防确认死循环)。"""
    verify_pass = '{"summary": "ok", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(
        [
            "# 计划",
            _send_call(),
            _send_call(),
            _send_call(),
            _send_call(),  # 第 4 次:超过上限,不执行
            "确认次数已达上限,未发布",
            verify_pass,
            "# 交付物",
        ]
    )
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, extras=(danger.spec(),)) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
        for _ in range(3):
            assert run["status"] == "awaiting_confirmation"
            run = client.post(
                f"/api/v1/runs/{run_id}/confirm",
                json={"action": "approve"},
                headers=ACCOUNT,
            ).json()
        assert run["status"] == "completed"
        assert len(danger.sent) == 3  # 上限 3 次,第 4 次请求被跳过


# ---- P1-A:确认时的授权范围(grant) ----


def _db_url(tmp_path) -> str:
    return _settings(
        tmp_path, "tools.db", llm_enabled=True, tools_enabled=True
    ).database_url


def _grants(tmp_path, conversation_id: str) -> list[str]:
    """直读会话授权表,返回工具名(按工具名排序)。"""

    async def read() -> list[str]:
        factory = create_session_factory(_db_url(tmp_path))
        try:
            async with session_scope(factory) as session:
                rows = await ConversationApprovalGrantRepository(
                    session
                ).list_for_conversation("t-1", conversation_id)
                return [row.tool for row in rows]
        finally:
            await factory.engine.dispose()

    return asyncio.run(read())


def _create_conversation(client) -> str:
    resp = client.post("/api/v1/conversations", json={}, headers=ACCOUNT)
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_conversation_run(client, conversation_id: str) -> str:
    resp = client.post(
        "/api/v1/runs",
        json={
            "title": "周报",
            "goal": "整理本周日程",
            "acceptance_criteria": ["日程已整理"],
            "conversation_id": conversation_id,
        },
        headers=ACCOUNT,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def test_confirm_with_always_grant_writes_account_policy(tmp_path) -> None:
    """approve + always:写账号级 auto 策略(§5.2)。"""
    verify_pass = '{"summary": "已发布", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(["# 计划", _send_call(), "已发布", verify_pass, "# 交付物"])
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, extras=(danger.spec(),)) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        assert (
            client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()[
                "status"
            ]
            == "awaiting_confirmation"
        )
        run = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve", "grant": "always"},
            headers=ACCOUNT,
        ).json()
        assert run["status"] == "completed"
        assert danger.sent == [{"title": "对外发布说明"}]
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


def test_confirm_with_session_grant_without_conversation_degrades(tmp_path) -> None:
    """run 无 conversation_id:session 降级 once,不落会话授权。"""
    verify_pass = '{"summary": "已发布", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(["# 计划", _send_call(), "已发布", verify_pass, "# 交付物"])
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, extras=(danger.spec(),)) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        run = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve", "grant": "session"},
            headers=ACCOUNT,
        ).json()
        assert run["status"] == "completed"
        assert danger.sent == [{"title": "对外发布说明"}]
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []

        async def grant_count() -> int:
            factory = create_session_factory(_db_url(tmp_path))
            try:
                async with session_scope(factory) as session:
                    rows = await ConversationApprovalGrantRepository(
                        session
                    ).list_scoped("t-1")
                    return len(rows)
            finally:
                await factory.engine.dispose()

        assert asyncio.run(grant_count()) == 0


def test_confirm_with_session_grant_on_conversation_run(tmp_path) -> None:
    """有会话的 run:session 授权落该会话(工具名来自 pending_writes)。"""
    verify_pass = '{"summary": "已发布", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(["# 计划", _send_call(), "已发布", verify_pass, "# 交付物"])
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, extras=(danger.spec(),)) as client:
        conversation_id = _create_conversation(client)
        run_id = _create_conversation_run(client, conversation_id)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        run = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve", "grant": "session"},
            headers=ACCOUNT,
        ).json()
        assert run["status"] == "completed"
        assert danger.sent == [{"title": "对外发布说明"}]
        assert _grants(tmp_path, conversation_id) == ["external_publish"]


def test_reject_ignores_grant(tmp_path) -> None:
    """reject + always:拒绝不放行,不落任何授权。"""
    verify_pass = '{"summary": "未发布", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(["# 计划", _send_call(), verify_pass, "# 交付物"])
    danger = FakeDangerTool()
    with _tool_client(tmp_path, fake, extras=(danger.spec(),)) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        run = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "reject", "grant": "always", "reason": "先别发"},
            headers=ACCOUNT,
        ).json()
        assert run["status"] == "completed"
        assert danger.sent == []
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []


def test_grant_without_pending_writes_only_warns(tmp_path) -> None:
    """确认闸事件缺失(无 pending_writes):落授权只告警,不抛异常、不写策略。"""
    verify_pass = '{"summary": "ok", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(["# 计划", "执行", verify_pass, "# 交付物"])
    with _tool_client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)

        async def apply() -> None:
            factory = create_session_factory(_db_url(tmp_path))
            try:
                async with session_scope(factory) as session:
                    run = await session.scalar(select(Run).where(Run.id == run_id))
                    assert run is not None
                    await _apply_grants(session, run, "always")
            finally:
                await factory.engine.dispose()

        asyncio.run(apply())
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []
