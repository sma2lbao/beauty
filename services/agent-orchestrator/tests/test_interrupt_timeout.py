"""中断超时 sweeper(P0-2):待审批/待确认无限期挂起时自动按拒绝收场。"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.repositories import (
    QUESTION_ANSWERED_EVENT,
    QUESTION_REQUIRED_EVENT,
    QUESTION_TIMED_OUT_EVENT,
)
from agent_orchestrator.services import interrupts
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import build_tools
from tests.conftest import (
    _IDP,
    TENANT,
    FakeDangerTool,
    FakeInteractionToolkit,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _settings,
    ask_reply,
)

DANGER_ARGS = '{"title": "对外发布说明"}'
VERIFY_PASS = '{"summary": "已跳过", "verdicts": {"日程已整理": true}}'


def _wait_status(
    client: TestClient, run_id: str, expected: str, *, deadline: float = 15.0
) -> dict:
    """轮询 run 状态(后台 worker/sweeper 推进,不能靠同步响应)。"""
    end = time.monotonic() + deadline
    run: dict = {}
    while time.monotonic() < end:
        run = client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()
        if run["status"] == expected:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run 未到 {expected}: {run}")


def _events(client: TestClient, run_id: str) -> list[dict]:
    """该 run 的完整事件列表(类型 + payload 断言用)。"""
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT)
    assert resp.status_code == 200
    return resp.json()


def test_approval_timeout_rejects_plan_and_allows_resume(tmp_path: Path) -> None:
    app = create_app(
        _settings(
            tmp_path,
            "approval-timeout.db",
            jobs_enabled=True,
            jobs_poll_interval_seconds=0.01,
            approval_timeout_seconds=1,
            interrupt_sweep_interval_seconds=0.05,
        ),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/runs",
            json={"title": "超时审批", "goal": "跑一下", "acceptance_criteria": []},
            headers=TENANT,
        )
        assert created.status_code == 201
        run_id = created.json()["id"]

        # worker 先把计划推到审批闸,再等 sweeper 用审批超时自动拒绝
        _wait_status(client, run_id, "awaiting_approval")
        blocked = _wait_status(client, run_id, "blocked")
        assert blocked["status_reason"] == interrupts.APPROVAL_TIMEOUT_REASON

        events = _events(client, run_id)
        types = [event["type"] for event in events]
        assert interrupts.TIMED_OUT_EVENT in types
        fired = next(e for e in events if e["type"] == interrupts.TIMED_OUT_EVENT)
        assert fired["payload"]["kind"] == "approval"
        assert fired["payload"]["timeout_seconds"] == 1
        assert "run.rejected.v1" in types
        # 超时收场后仍可 resume 重新进闸(与人工拒绝同语义);
        # jobs 模式下 resume 只入队 plan job,状态由 worker 推进
        resumed = client.post(f"/api/v1/runs/{run_id}/resume", headers=TENANT)
        assert resumed.status_code == 200
        _wait_status(client, run_id, "awaiting_approval")


def _send_call() -> LLMReply:
    """一次请求两个工具:只读检索(先执行)+ 危险写(等确认)。"""
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


def test_confirmation_timeout_rejects_danger_write_and_completes(
    tmp_path: Path,
) -> None:
    fake = FakeLLM(
        [
            "# 计划",
            _send_call(),  # execute:危险写 → 中断等确认
            # 确认被拒绝 → 直连 verify(不执行写、不回 execute)
            VERIFY_PASS,
            "# 交付物",
        ]
    )
    danger = FakeDangerTool()
    knowledge = FakeKnowledge()
    app = create_app(
        _settings(
            tmp_path,
            "confirmation-timeout.db",
            llm_enabled=True,
            tools_enabled=True,
            jobs_enabled=True,
            jobs_poll_interval_seconds=0.01,
            confirmation_timeout_seconds=1,
            interrupt_sweep_interval_seconds=0.05,
        ),
        llm_client=fake,
        toolkit_factory=lambda tenant: build_tools(
            tenant, knowledge=knowledge
        ).extended([danger.spec()]),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        run_id = _create_run(client)

        _wait_status(client, run_id, "awaiting_approval")
        approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
        assert approved.status_code == 200
        _wait_status(client, run_id, "awaiting_confirmation")
        # 确认等待期间危险写绝不能执行
        assert danger.sent == []

        # sweeper 按确认超时自动拒绝 → 跳过写操作,图继续验收直到完成
        completed = _wait_status(client, run_id, "completed")
        assert completed["status"] == "completed"
        assert danger.sent == []

        events = _events(client, run_id)
        types = [event["type"] for event in events]
        assert interrupts.TIMED_OUT_EVENT in types
        fired = next(e for e in events if e["type"] == interrupts.TIMED_OUT_EVENT)
        assert fired["payload"]["kind"] == "confirmation"
        assert fired["payload"]["timeout_seconds"] == 1
        received = next(
            e for e in events if e["type"] == "run.confirmation.received.v1"
        )
        assert received["payload"]["action"] == "reject"
        # 危险写没有执行,但只读工具已执行过
        assert "external_publish" not in [
            e["payload"]["name"] for e in events if e["type"] == "run.tool.called.v1"
        ]


def test_question_timeout_declines_and_resumes(tmp_path: Path) -> None:
    """待回答问题超时:emit timed_out + answered(decline),worker 恢复图继续。"""

    fake = FakeLLM(
        [
            "# 计划",
            ask_reply(),  # execute:发起提问 → ask_gate 中断
            "未获回答按已有信息继续",  # 超时放弃后模型拿固定文案继续
            VERIFY_PASS,
            "# 交付物",
        ]
    )
    toolkit = FakeInteractionToolkit()
    app = create_app(
        _settings(
            tmp_path,
            "question-timeout.db",
            llm_enabled=True,
            tools_enabled=True,
            jobs_enabled=True,
            jobs_poll_interval_seconds=0.01,
            question_timeout_seconds=1,
            interrupt_sweep_interval_seconds=0.05,
        ),
        llm_client=fake,
        toolkit_factory=lambda tenant: toolkit,
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        _wait_status(client, run_id, "awaiting_approval")
        approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
        assert approved.status_code == 200
        _wait_status(client, run_id, "awaiting_input")

        # sweeper 按提问超时自动放弃 → worker 推图到完成
        completed = _wait_status(client, run_id, "completed")
        assert completed["status"] == "completed"
        # 提问从未走 execute(中断前零副作用)
        assert toolkit.executed == []

        events = _events(client, run_id)
        required = next(e for e in events if e["type"] == QUESTION_REQUIRED_EVENT)[
            "payload"
        ]
        fired = next(e for e in events if e["type"] == interrupts.TIMED_OUT_EVENT)
        assert fired["payload"]["kind"] == "question"
        assert fired["payload"]["timeout_seconds"] == 1
        assert fired["payload"]["reason"] == interrupts.QUESTION_TIMEOUT_REASON
        timed_out = next(e for e in events if e["type"] == QUESTION_TIMED_OUT_EVENT)[
            "payload"
        ]
        assert timed_out["question_id"] == required["question_id"]
        assert timed_out["expires_at"] == required["expires_at"]
        answered = [e for e in events if e["type"] == QUESTION_ANSWERED_EVENT]
        assert len(answered) == 1
        assert answered[0]["payload"] == {
            "question_id": required["question_id"],
            "answers": [],
            "action": "decline",
            "reason": "timeout",
            "answered_by": "system",
        }
