"""评测执行器:每用例独立进程内应用 + 临时 sqlite(设计 §2.1)。

驱动口径与黄金 run 一致:用 TestClient 在进程内打对外的 HTTP 契约
(create_app 注入脚本化模型/合成工具,不起端口、零网络),指标全部从
run 事件、交付物、SSE 与脚本化模型的调用计数里取——不 mock 内部服务,
也不读图状态,保证 eval 测的是"用户能看到的链路"。
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app

from .fakes import (
    ACCOUNT,
    FakeDangerTool,
    FakeKnowledge,
    NetworkGuard,
    ScriptedLLM,
    eval_settings,
    make_toolkit_factory,
    reset_process_state,
    token_verifier,
)
from .suites import (
    STEP_APPROVE_PLAN,
    STEP_CANCEL_RUN,
    STEP_CONFIRM_ALL,
    STEP_CREATE_CONVERSATION,
    STEP_CREATE_RUN,
    STEP_FORK_RUN,
    STEP_SEND_CHAT,
    STEP_WAIT_TERMINAL,
    Case,
    Suite,
)

#: run 终态(§6.2);wait_terminal 等到其中之一即停。
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "blocked"})
#: 确认闸最多循环几次(计划审批 + 危险操作确认,留够余量后视为异常)。
MAX_CONFIRM_LOOPS = 6
#: wait_terminal 的轮询上限(秒);进程内图是同步执行的,正常不会真的等。
WAIT_TERMINAL_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.01


@dataclass
class CaseResult:
    """一条用例的采集结果(JSON 报告与断言共用同一形状)。"""

    suite: str
    case_id: str
    ok: bool = False
    duration_ms: float = 0.0
    run_status: str | None = None
    events: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    #: 每次工具调用的结构化记录(名称、参数、结果元数据);tool_calls 保留名称列表。
    tool_call_details: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    deliverables: int = 0
    failures: list[str] = field(default_factory=list)
    reply: str = ""
    #: 多轮对话逐轮回复;单轮用例长度为 1。
    replies: list[str] = field(default_factory=list)
    #: 每次模型调用的消息序列(JSON 文本),用于验证多轮上下文确实注入。
    prompts: list[str] = field(default_factory=list)
    #: 最后一个 run 的只读快照(含 parent_run_id/parent_kind/title)。
    run_detail: dict[str, Any] = field(default_factory=dict)
    #: fork 前的父 run 快照;非 fork 用例为空。
    parent_run_detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "ok": self.ok,
            "duration_ms": round(self.duration_ms, 2),
            "run_status": self.run_status,
            "events": list(self.events),
            "tool_calls": list(self.tool_calls),
            "tool_call_details": [dict(detail) for detail in self.tool_call_details],
            "run_detail": dict(self.run_detail),
            "parent_run_detail": dict(self.parent_run_detail),
            "tokens": self.tokens,
            "deliverables": self.deliverables,
            "failures": list(self.failures),
        }


def parse_sse(text: str) -> list[tuple[str, dict[str, Any] | None]]:
    """SSE 文本 -> [(event, data)];忽略 keep-alive 注释帧。"""
    frames: list[tuple[str, dict[str, Any] | None]] = []
    for block in text.split("\n\n"):
        if not block or block.lstrip().startswith(":"):
            continue
        event: str | None = None
        data: str | None = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event:
            frames.append((event, json.loads(data) if data else None))
    return frames


def _json(response: Any, *, expect: int = 200) -> Any:
    if response.status_code != expect:
        raise RuntimeError(
            f"HTTP {response.status_code}(期望 {expect}):{response.text[:200]}"
        )
    return response.json()


def _run(client: TestClient, run_id: str) -> dict[str, Any]:
    return _json(client.get(f"/api/v1/runs/{run_id}", headers=ACCOUNT))


def _create_conversation(client: TestClient) -> str:
    payload = _json(
        client.post("/api/v1/conversations", json={}, headers=ACCOUNT),
        expect=201,
    )
    return str(payload["id"])


def _send_chat(client: TestClient, conversation_id: str, content: str) -> str:
    """流式直答:拼 delta 正文,done 帧的 reply 作为兜底。"""
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": content, "mode": "chat"},
        headers=ACCOUNT,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"对话 SSE 期望 200,实际 {response.status_code}:{response.text[:200]}"
        )
    frames = parse_sse(response.text)
    parts = [
        str((data or {}).get("text", "")) for event, data in frames if event == "delta"
    ]
    if parts:
        return "".join(parts)
    done = next((data for event, data in frames if event == "done"), None)
    reply = (done or {}).get("reply") or {}
    return str(reply.get("content") or "")


def _create_run(client: TestClient, case: Case) -> str:
    payload = _json(
        client.post(
            "/api/v1/runs",
            json={
                "title": case.resolved_title()[:200],
                "goal": case.goal,
            },
            headers=ACCOUNT,
        ),
        expect=201,
    )
    return str(payload["id"])


def _apply_policies(client: TestClient, case: Case) -> None:
    if not case.policies:
        return
    _json(
        client.put(
            "/api/v1/approval-policies",
            json={"policies": [dict(policy) for policy in case.policies]},
            headers=ACCOUNT,
        )
    )


def _approve_plan(client: TestClient, run_id: str) -> None:
    """规划一次,并在停在计划审批时放行。"""
    plan = _json(client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT))
    status = plan.get("status")
    if status == "awaiting_approval":
        _json(client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT))


def _cancel_run(client: TestClient, run_id: str) -> None:
    """取消当前 run;只允许非终态,终态会被服务端拒绝。"""
    _json(
        client.post(
            f"/api/v1/runs/{run_id}/cancel",
            json={"reason": "评测取消"},
            headers=ACCOUNT,
        )
    )


def _fork_run(client: TestClient, run_id: str) -> str:
    """从当前 run 分叉出 pending 子 run,返回子 run id。"""
    payload = _json(
        client.post(
            f"/api/v1/runs/{run_id}/fork",
            json={},
            headers=ACCOUNT,
        ),
        expect=201,
    )
    child_id = payload.get("id")
    if not child_id:
        raise RuntimeError("fork 响应缺少子 run id")
    return str(child_id)


def _confirm_all(client: TestClient, run_id: str) -> None:
    """循环确认:计划审批(防御性)与危险操作确认直到没有待确认闸。"""
    for _ in range(MAX_CONFIRM_LOOPS):
        run = _run(client, run_id)
        status = run.get("status")
        if status == "awaiting_confirmation":
            _json(
                client.post(
                    f"/api/v1/runs/{run_id}/confirm",
                    json={"action": "approve"},
                    headers=ACCOUNT,
                )
            )
        elif status == "awaiting_approval":
            _json(client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT))
        else:
            return


def _wait_terminal(client: TestClient, run_id: str) -> None:
    deadline = time.monotonic() + WAIT_TERMINAL_SECONDS
    while True:
        status = _run(client, run_id).get("status")
        if status in TERMINAL_STATUSES:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"等待终态超时({WAIT_TERMINAL_SECONDS}s),实际状态 {status!r}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)


def _tool_call_detail(payload: Mapping[str, Any]) -> dict[str, Any]:
    """把工具事件 payload 压成可断言的结构化记录,参数尽量还原成对象。"""
    arguments = payload.get("arguments")
    parsed: Any = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            parsed = arguments
    return {
        "name": str(payload.get("name") or ""),
        "arguments": parsed,
        "ok": bool(payload.get("ok")),
        "risk": str(payload.get("risk") or ""),
        "decision": str(payload.get("decision") or ""),
        "error_code": payload.get("error_code"),
        "duration_ms": int(payload.get("duration_ms") or 0),
    }


def _run_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """RunRead 里与评测断言有关的只读子集;不把 datetime 等不可序列化字段带出。"""
    status = payload.get("status")
    return {
        "id": str(payload.get("id") or ""),
        "title": str(payload.get("title") or ""),
        "status": str(status) if status is not None else None,
        "goal": str(payload.get("goal") or ""),
        "parent_run_id": payload.get("parent_run_id"),
        "parent_kind": payload.get("parent_kind"),
        "priority": payload.get("priority"),
        "timeout_seconds": payload.get("timeout_seconds"),
    }


def _collect(
    client: TestClient,
    case: Case,
    run_id: str | None,
    llm: ScriptedLLM,
    *,
    parent_run_id: str | None = None,
) -> tuple[
    str | None,
    list[str],
    list[str],
    list[dict[str, Any]],
    int,
    dict[str, Any],
    dict[str, Any],
]:
    """采集状态/事件/工具调用/交付物数量与 run 快照(chat 用例没有 run)。"""
    if run_id is None:
        return None, [], [], [], 0, {}, {}
    run_payload = _run(client, run_id)
    status = str(run_payload.get("status") or "") or None
    rows = _json(client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT))
    events = [str(row.get("type") or "") for row in rows]
    tool_rows = [row for row in rows if row.get("type") == "run.tool.called.v1"]
    tool_calls = [
        str((row.get("payload") or {}).get("name") or "") for row in tool_rows
    ]
    tool_call_details = [
        _tool_call_detail(row.get("payload") or {}) for row in tool_rows
    ]
    artifacts = _json(client.get(f"/api/v1/runs/{run_id}/artifacts", headers=ACCOUNT))
    parent_detail = _run_snapshot(_run(client, parent_run_id)) if parent_run_id else {}
    del case, llm
    return (
        status,
        events,
        tool_calls,
        tool_call_details,
        len(artifacts),
        _run_snapshot(run_payload),
        parent_detail,
    )


def _execute_steps(client: TestClient, case: Case, metrics: dict[str, Any]) -> None:
    """按 steps 顺序驱动;未声明步骤的用例走 kind 默认步骤。"""
    _apply_policies(client, case)
    conversation_id: str | None = None
    run_id: str | None = None
    forked_from: str | None = None
    chat_turn = 0
    for step in case.resolved_steps():
        if step == STEP_CREATE_CONVERSATION:
            conversation_id = _create_conversation(client)
        elif step == STEP_SEND_CHAT:
            if conversation_id is None:
                raise RuntimeError("send_chat 之前需要 create_conversation")
            reply = _send_chat(
                client, conversation_id, case.message_for_turn(chat_turn)
            )
            metrics["reply"] = reply
            metrics["replies"].append(reply)
            chat_turn += 1
        elif step == STEP_CREATE_RUN:
            run_id = _create_run(client, case)
        elif step == STEP_APPROVE_PLAN:
            if run_id is None:
                raise RuntimeError("approve_plan 之前需要 create_run")
            _approve_plan(client, run_id)
        elif step == STEP_CONFIRM_ALL:
            if run_id is None:
                raise RuntimeError("confirm_all 之前需要 create_run")
            _confirm_all(client, run_id)
        elif step == STEP_WAIT_TERMINAL:
            if run_id is None:
                raise RuntimeError("wait_terminal 之前需要 create_run")
            _wait_terminal(client, run_id)
        elif step == STEP_CANCEL_RUN:
            if run_id is None:
                raise RuntimeError("cancel_run 之前需要 create_run")
            _cancel_run(client, run_id)
        elif step == STEP_FORK_RUN:
            if run_id is None:
                raise RuntimeError("fork_run 之前需要 create_run")
            forked_from = run_id
            run_id = _fork_run(client, run_id)
        else:  # pragma: no cover - 用例 schema 已校验过步骤名
            raise RuntimeError(f"未知步骤:{step}")
    metrics["run_id"] = run_id
    metrics["forked_from"] = forked_from
    metrics["conversation_id"] = conversation_id


def run_case(suite_name: str, case: Case) -> CaseResult:
    """跑一条用例:临时库、独立应用、独立进程级状态,失败不中断其它用例。"""
    started = time.perf_counter()
    result = CaseResult(suite=suite_name, case_id=case.id)
    llm = ScriptedLLM(case.llm_script)
    knowledge = FakeKnowledge()
    danger = FakeDangerTool()
    metrics: dict[str, Any] = {"reply": "", "replies": []}
    failures: list[str] = []
    reset_process_state()
    try:
        with tempfile.TemporaryDirectory(prefix="eval-") as tmp:
            settings = eval_settings(Path(tmp) / "eval.db")
            app = create_app(
                settings,
                llm_client=llm,
                toolkit_factory=make_toolkit_factory(
                    knowledge=knowledge, danger=danger
                ),
                token_verifier=token_verifier(),
            )
            with NetworkGuard() as guard, TestClient(app) as client:
                _execute_steps(client, case, metrics)
                (
                    status,
                    events,
                    tool_calls,
                    tool_call_details,
                    deliverables,
                    run_detail,
                    parent_detail,
                ) = _collect(
                    client,
                    case,
                    metrics.get("run_id"),
                    llm,
                    parent_run_id=metrics.get("forked_from"),
                )
            if guard.violations:  # pragma: no cover - 正常链路不会触发
                failures.append(f"检测到网络连接:{guard.violations}")
            result.run_status = status
            result.events = events
            result.tool_calls = tool_calls
            result.tool_call_details = tool_call_details
            result.deliverables = deliverables
            result.run_detail = run_detail
            result.parent_run_detail = parent_detail
    except Exception as exc:  # noqa: BLE001 - 用例异常必须落成失败原因
        failures.append(f"执行异常:{type(exc).__name__}: {exc}")
    finally:
        reset_process_state()
    result.reply = str(metrics.get("reply") or "")
    result.replies = [str(item) for item in metrics.get("replies") or []]
    result.prompts = [
        json.dumps(messages, ensure_ascii=False, default=str) for messages in llm.calls
    ]
    result.tokens = llm.tokens
    cache = token_cache(case, result)
    result.failures = [*failures, *cache]
    result.ok = not result.failures
    result.duration_ms = (time.perf_counter() - started) * 1000
    return result


def token_cache(case: Case, result: CaseResult) -> list[str]:
    """断言评估:放在最后,异常路径下也能对已采集指标做局部比对。"""
    from .assertions import evaluate

    return evaluate(case.expect, result)


def run_suites(suites: Sequence[Suite]) -> list[CaseResult]:
    """顺序跑完所有套件(顺序是契约:报告与基线按同一顺序比对)。"""
    results: list[CaseResult] = []
    for suite in suites:
        for case in suite.cases:
            results.append(run_case(suite.name, case))
    return results
