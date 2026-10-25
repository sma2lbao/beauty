"""ask_question 交互工具全链路(§10):图内中断、回答恢复、拒绝与 jobs 路径。

覆盖契约:

- execute 命中 interaction 调用只收集问题(ask_gate 中断前零副作用);
- interrupt payload 固定为 {kind:"question", question_id, questions,
  allow_custom, expires_at};
- resume 后答案/拒绝归一成 tool-role 消息写回,模型下一轮据此继续;
- POST /answer 的状态守卫与错误码(run.invalid_state / run.question_mismatch /
  run.question_expired);
- jobs 模式:API 入队 answer job,worker 恢复图且不重复 emit answered 事件。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_orchestrator.graph.nodes import (
    MAX_ASK_CYCLES,
    QUESTION_DECLINED_REPLY,
)
from agent_orchestrator.main import create_app
from agent_orchestrator.repositories import (
    QUESTION_ANSWERED_EVENT,
    QUESTION_REQUIRED_EVENT,
    TOOL_CALLED_EVENT,
)
from agent_orchestrator.services.llm import LLMReply, ToolCall
from tests.conftest import (
    _IDP,
    DEFAULT_ASK_QUESTIONS,
    TENANT,
    FakeInteractionToolkit,
    FakeLLM,
    _create_run,
    _settings,
    ask_reply,
)

PLAN = "# 计划"
DONE = "已按已有信息整理日程"
VERIFY_PASS = '{"summary": "已整理", "verdicts": {"日程已整理": true}}'
ARTIFACT = "# 交付物"


def _ask_client(
    tmp_path: Path,
    fake: FakeLLM,
    toolkit: FakeInteractionToolkit,
    *,
    name: str = "ask.db",
    **overrides: Any,
) -> TestClient:
    """LLM + 交互工具箱的 app:task 图经 with_interaction 后才看得到 ask_question。"""
    app = create_app(
        _settings(tmp_path, name, llm_enabled=True, tools_enabled=True, **overrides),
        llm_client=fake,
        toolkit_factory=lambda tenant: toolkit,
        token_verifier=_IDP.verifier(),
    )
    return TestClient(app)


def _events(client: TestClient, run_id: str) -> list[dict]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT)
    assert resp.status_code == 200
    return resp.json()


def _of_type(events: list[dict], event_type: str) -> list[dict]:
    return [event for event in events if event["type"] == event_type]


def _single_required(events: list[dict]) -> dict:
    required = _of_type(events, QUESTION_REQUIRED_EVENT)
    assert len(required) == 1, required
    return required[0]["payload"]


def _latest_required(events: list[dict]) -> dict:
    required = _of_type(events, QUESTION_REQUIRED_EVENT)
    assert required
    return required[-1]["payload"]


def _tool_messages(messages: list[dict]) -> list[dict]:
    return [message for message in messages if message.get("role") == "tool"]


def _tool_contents(messages: list[dict]) -> list[str]:
    return [str(message.get("content") or "") for message in _tool_messages(messages)]


def _wait_status(
    client: TestClient, run_id: str, expected: str, *, deadline: float = 15.0
) -> dict:
    """轮询 run 状态(jobs 模式由后台 worker 推进)。"""
    end = time.monotonic() + deadline
    run: dict = {}
    while time.monotonic() < end:
        run = client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()
        if run["status"] == expected:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run 未到 {expected}: {run}")


def _plan_and_approve(client: TestClient, run_id: str) -> Any:
    assert client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).status_code == 200
    return client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)


def _start_ask(client: TestClient) -> str:
    """建 run → 规划 → 放行;同步返回时图应停在 awaiting_input。"""
    run_id = _create_run(client)
    resp = _plan_and_approve(client, run_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "awaiting_input"
    return run_id


def _answer(
    client: TestClient,
    run_id: str,
    question_id: str,
    *,
    action: str = "answer",
    answers: list[dict] | None = None,
) -> Any:
    return client.post(
        f"/api/v1/runs/{run_id}/answer",
        json={
            "question_id": question_id,
            "action": action,
            "answers": answers or [],
        },
        headers=TENANT,
    )


def test_answer_resumes_execution_and_completes(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    fake = FakeLLM([PLAN, ask_reply(), DONE, VERIFY_PASS, ARTIFACT])
    with _ask_client(tmp_path, fake, toolkit) as client:
        run_id = _start_ask(client)
        payload = _single_required(_events(client, run_id))
        question_id = payload["question_id"]

        # task 图打开 interaction 可见性;中断前零副作用(ask 不走 execute)
        assert toolkit.executed == []
        assert any(
            item["function"]["name"] == "ask_question"
            for item in fake.tools_seen[1] or []
        )
        assert any(name == "ask_question" for name, _ in toolkit.validated)
        # interrupt payload 是完整契约,不夹带内部 _history
        assert set(payload) == {
            "question_id",
            "questions",
            "allow_custom",
            "expires_at",
        }
        assert len(question_id) == 32
        assert payload["questions"] == DEFAULT_ASK_QUESTIONS
        assert payload["allow_custom"] is True
        assert payload["expires_at"]

        resp = _answer(
            client,
            run_id,
            question_id,
            answers=[
                {"index": 0, "labels": ["每周"]},
                {"index": 1, "labels": ["周三"], "custom": "带纪要"},
            ],
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["accepted"] is True
        assert body["run"]["status"] == "completed"

        events = _events(client, run_id)
        answered = _of_type(events, QUESTION_ANSWERED_EVENT)
        assert len(answered) == 1
        assert answered[0]["payload"] == {
            "question_id": question_id,
            "answers": [
                {"index": 0, "labels": ["每周"], "custom": None},
                {"index": 1, "labels": ["周三"], "custom": "带纪要"},
            ],
            "action": "answer",
            "reason": None,
            "answered_by": None,
        }
        assert _of_type(events, "run.completed.v1")
        tool_messages = _tool_messages(fake.calls[2])
        contents = [str(message["content"]) for message in tool_messages]
        assert any(
            "用户已回答你的提问:" in content
            and "1. 汇报周期? -> 每周" in content
            and "2. 同步时间? -> 周三、带纪要" in content
            for content in contents
        )
        assert any(message["tool_call_id"] == "call_ask" for message in tool_messages)
        assert toolkit.executed == []


def test_decline_writes_fixed_reply_and_completes(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    fake = FakeLLM([PLAN, ask_reply(), DONE, VERIFY_PASS, ARTIFACT])
    with _ask_client(tmp_path, fake, toolkit) as client:
        run_id = _start_ask(client)
        question_id = _single_required(_events(client, run_id))["question_id"]
        resp = _answer(client, run_id, question_id, action="decline")
        assert resp.status_code == 202, resp.text
        assert resp.json()["run"]["status"] == "completed"
        payload = _of_type(_events(client, run_id), QUESTION_ANSWERED_EVENT)[0][
            "payload"
        ]
        assert payload["action"] == "decline"
        assert payload["answers"] == []
        assert QUESTION_DECLINED_REPLY in _tool_contents(fake.calls[2])


def test_answer_before_question_is_invalid_state(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    fake = FakeLLM([PLAN])
    with _ask_client(tmp_path, fake, toolkit) as client:
        run_id = _create_run(client)
        assert (
            client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).status_code
            == 200
        )
        assert (
            client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()["status"]
            == "awaiting_approval"
        )
        resp = _answer(client, run_id, "whatever")
        assert resp.status_code == 409
        assert resp.json()["code"] == "run.invalid_state"


def test_answer_with_stale_question_id_conflicts(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    fake = FakeLLM([PLAN, ask_reply()])
    with _ask_client(tmp_path, fake, toolkit) as client:
        run_id = _start_ask(client)
        resp = _answer(client, run_id, "stale-question-id")
        assert resp.status_code == 409
        assert resp.json()["code"] == "run.question_mismatch"
        # 冲突走事务回滚:状态保持 awaiting_input,可以重新回答
        assert (
            client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()["status"]
            == "awaiting_input"
        )


def test_answer_after_question_expired_conflicts(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    fake = FakeLLM([PLAN, ask_reply()])
    with _ask_client(tmp_path, fake, toolkit, question_timeout_seconds=1) as client:
        run_id = _start_ask(client)
        question_id = _single_required(_events(client, run_id))["question_id"]
        time.sleep(1.1)
        resp = _answer(client, run_id, question_id)
        assert resp.status_code == 409
        assert resp.json()["code"] == "run.question_expired"
        assert (
            client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()["status"]
            == "awaiting_input"
        )


def test_ask_does_not_execute_other_calls_in_same_round(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    ask_call = ToolCall(
        id="call_ask",
        name="ask_question",
        arguments=json.dumps({"questions": DEFAULT_ASK_QUESTIONS}, ensure_ascii=False),
    )
    other = ToolCall(id="call_other", name="search_knowledge", arguments="{}")
    fake = FakeLLM(
        [
            PLAN,
            LLMReply(content="", tool_calls=(ask_call, other)),
            DONE,
            VERIFY_PASS,
            ARTIFACT,
        ]
    )
    with _ask_client(tmp_path, fake, toolkit) as client:
        run_id = _start_ask(client)
        question_id = _single_required(_events(client, run_id))["question_id"]
        # 同轮其余工具一律不执行(ask_gate 中断前零副作用)
        assert toolkit.executed == []
        resp = _answer(
            client, run_id, question_id, answers=[{"index": 0, "labels": ["每周"]}]
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["run"]["status"] == "completed"
        records = {
            event["payload"]["name"]: event["payload"]
            for event in _of_type(_events(client, run_id), TOOL_CALLED_EVENT)
        }
        assert records["search_knowledge"]["decision"] == "denied"
        assert "本轮包含需要用户回答的提问" in records["search_knowledge"]["detail"]
        assert "已向用户发起提问" in records["ask_question"]["detail"]
        assert toolkit.executed == []


def test_invalid_ask_arguments_do_not_gate(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    bad_call = ToolCall(
        id="call-bad",
        name="ask_question",
        arguments=json.dumps({"questions": []}, ensure_ascii=False),
    )
    fake = FakeLLM(
        [
            PLAN,
            LLMReply(content="", tool_calls=(bad_call,)),
            DONE,
            VERIFY_PASS,
            ARTIFACT,
        ]
    )
    with _ask_client(tmp_path, fake, toolkit) as client:
        run_id = _create_run(client)
        resp = _plan_and_approve(client, run_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"
        events = _events(client, run_id)
        assert _of_type(events, QUESTION_REQUIRED_EVENT) == []
        assert _of_type(events, "run.completed.v1")
        assert toolkit.executed == []
        contents = _tool_contents(fake.calls[2])
        assert any(
            "提问参数不合法" in content and "至少需要 1 项" in content
            for content in contents
        )


def test_denied_by_policy_does_not_gate(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    fake = FakeLLM([PLAN, ask_reply(), DONE, VERIFY_PASS, ARTIFACT])
    with _ask_client(tmp_path, fake, toolkit) as client:
        replaced = client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "ask_question", "action": "deny"}]},
            headers=TENANT,
        )
        assert replaced.status_code == 200, replaced.text
        run_id = _create_run(client)
        resp = _plan_and_approve(client, run_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"
        assert _of_type(_events(client, run_id), QUESTION_REQUIRED_EVENT) == []
        contents = _tool_contents(fake.calls[2])
        assert any("被租户审批策略禁止使用" in content for content in contents)


def test_ask_cycle_limit_stops_asking(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    fake = FakeLLM(
        [
            PLAN,
            ask_reply(),
            ask_reply(),
            ask_reply(),
            ask_reply(),
            DONE,
            VERIFY_PASS,
            ARTIFACT,
        ]
    )
    with _ask_client(tmp_path, fake, toolkit) as client:
        run_id = _start_ask(client)
        for _ in range(MAX_ASK_CYCLES - 1):
            question_id = _latest_required(_events(client, run_id))["question_id"]
            resp = _answer(
                client, run_id, question_id, answers=[{"index": 0, "labels": ["每周"]}]
            )
            assert resp.status_code == 202, resp.text
            assert resp.json()["run"]["status"] == "awaiting_input"
        question_id = _latest_required(_events(client, run_id))["question_id"]
        resp = _answer(
            client, run_id, question_id, answers=[{"index": 0, "labels": ["每周"]}]
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["run"]["status"] == "completed"
        events = _events(client, run_id)
        assert len(_of_type(events, QUESTION_REQUIRED_EVENT)) == MAX_ASK_CYCLES
        contents = _tool_contents(fake.calls[5])
        assert any(
            f"提问次数已达上限({MAX_ASK_CYCLES})" in content for content in contents
        )
        assert toolkit.executed == []


def test_jobs_mode_answer_resumes_via_worker(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    fake = FakeLLM([PLAN, ask_reply(), DONE, VERIFY_PASS, ARTIFACT])
    with _ask_client(
        tmp_path,
        fake,
        toolkit,
        name="ask-jobs.db",
        jobs_enabled=True,
        jobs_poll_interval_seconds=0.01,
    ) as client:
        run_id = _create_run(client)
        assert (
            client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).status_code
            == 200
        )
        _wait_status(client, run_id, "awaiting_approval")
        assert (
            client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).status_code
            == 200
        )
        _wait_status(client, run_id, "awaiting_input")
        question_id = _single_required(_events(client, run_id))["question_id"]
        resp = _answer(
            client, run_id, question_id, answers=[{"index": 0, "labels": ["每周"]}]
        )
        assert resp.status_code == 202, resp.text
        # API 侧只推进到 running 并入队,真正的图恢复由 worker 做
        assert resp.json()["run"]["status"] == "running"
        _wait_status(client, run_id, "completed")
        events = _events(client, run_id)
        # worker 用 from_job 恢复,不重复 emit answered 事件
        assert len(_of_type(events, QUESTION_ANSWERED_EVENT)) == 1
        assert len(_of_type(events, QUESTION_REQUIRED_EVENT)) == 1
        assert _of_type(events, "run.completed.v1")
        assert any(
            "用户已回答你的提问:" in content
            for content in _tool_contents(fake.calls[2])
        )
        assert toolkit.executed == []


def test_answer_request_validation(tmp_path: Path) -> None:
    toolkit = FakeInteractionToolkit()
    fake = FakeLLM([PLAN, ask_reply()])
    with _ask_client(tmp_path, fake, toolkit) as client:
        run_id = _start_ask(client)
        empty_id = client.post(
            f"/api/v1/runs/{run_id}/answer",
            json={"question_id": "", "action": "answer"},
            headers=TENANT,
        )
        assert empty_id.status_code == 422
        too_many_labels = client.post(
            f"/api/v1/runs/{run_id}/answer",
            json={
                "question_id": "q",
                "action": "answer",
                "answers": [
                    {"index": 0, "labels": [str(i) for i in range(5)]},
                ],
            },
            headers=TENANT,
        )
        assert too_many_labels.status_code == 422
