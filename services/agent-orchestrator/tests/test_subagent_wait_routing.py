"""L2 等待接线(2026-11-18):写确认与 spawn 同回合、spawn 被策略拦下都不丢回执。

三条路径在本文件覆盖:
- 节点单测:execute 同回合「待确认写 + 已入队子代理」返回同时带 pending_writes 与
  pending_subagents(此前只带 pending_writes,回执被丢,确认完成后直接跳 verify);
- 节点单测:tool_exec 执行人工确认的 spawn 后收集回执,并与 state 里已有回执合并;
- 路由单测 + HTTP e2e:确认闸之后不再直接回工具循环/验收,而是进 subagent_wait。

e2e 用真实 app + 后台 job worker(jobs_enabled=True),覆盖两种上游:
① 同回合默认策略(danger→confirm、spawn→auto);② 策略把 spawn 也设成 confirm。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_orchestrator.graph.builder import (
    route_after_confirm,
    route_after_tool_exec,
)
from agent_orchestrator.graph.nodes import make_execute_node, make_tool_exec_node
from agent_orchestrator.graph.nodes.subagent_wait import PENDING_KEY
from agent_orchestrator.graph.ports import GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.main import create_app
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.subagents import build_spawn_tool
from agent_orchestrator.services.tools import build_tools
from tests.conftest import (
    _IDP,
    ACCOUNT,
    FakeDangerTool,
    FakeLLM,
    _create_run,
    _settings,
)

DANGER_ARGS = '{"title": "对外发布说明"}'
VERIFY_PASS = '{"summary": "已发布", "verdicts": {"日程已整理": true}}'
#: 子 run 的 goal(即 spawn 的 task)带标记,便于父/子共用一个 LLM 时区分脚本。
CHILD_MARK = "CHILD-MARK"
SPAWN_ARGS = json.dumps(
    {
        "title": "调研竞品",
        "task": f"{CHILD_MARK}:整理三家定价页差异",
        "agent_name": "调研员",
        "instructions": "只做调研,不改代码",
        "summary": "整理三家定价页差异",
    },
    ensure_ascii=False,
)
SPAWN_ACK = json.dumps(
    {
        "ok": True,
        "pending": True,
        "subagent_run_id": "child-1",
        "status": "queued",
        "title": "调研竞品",
        "summary": "",
        "note": "子代理已入队运行;父任务挂起等待,子代理结束后自动继续",
    },
    ensure_ascii=False,
)


def _state(**overrides: Any) -> RunGraphState:
    state: dict[str, Any] = {
        "run_id": "run-1",
        "account_id": "t-1",
        "title": "周报",
        "goal": "整理本周日程",
        "acceptance_criteria": ["日程已整理"],
    }
    state.update(overrides)
    return state  # type: ignore[return-value]


def _config() -> dict[str, Any]:
    return {"configurable": {GRAPH_RUNTIME_KEY: GraphRuntime(ports=GraphPorts())}}


class _MixedToolkit:
    """最小 ToolkitPort:一个危险写 + 一个 spawn,按名字回固定信封。"""

    def __init__(self, ack: str = SPAWN_ACK) -> None:
        self.ack = ack
        self.executed: list[str] = []

    def __len__(self) -> int:
        return 2

    def has_dangerous(self) -> bool:
        return True

    def risk(self, name: str) -> str:
        return "danger" if name == "external_publish" else "write"

    def kind(self, name: str) -> str:
        return "function"

    def validate_call(self, name: str, arguments: str) -> list[str]:
        return []

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": "external_publish"}, {"name": "spawn_subagent"}]

    def extended(self, extras: Any) -> _MixedToolkit:
        return self

    def restricted(self, allowed: list[str] | None) -> _MixedToolkit:
        return self

    def with_interaction(self) -> _MixedToolkit:
        return self

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = True,
        idempotency_key: str | None = None,
    ) -> str:
        self.executed.append(name)
        if name == "spawn_subagent":
            return self.ack
        title = json.loads(arguments).get("title", "")
        return json.dumps({"ok": True, "published": title}, ensure_ascii=False)


def _mixed_call() -> LLMReply:
    """同回合:危险写落 pending,spawn 直接执行(入队)。"""
    return LLMReply(
        content="",
        tool_calls=(
            ToolCall(id="call-w1", name="external_publish", arguments=DANGER_ARGS),
            ToolCall(id="call-s1", name="spawn_subagent", arguments=SPAWN_ARGS),
        ),
    )


def _spawn_call() -> LLMReply:
    """只 spawn:策略设 confirm 时它会整批进 pending_writes。"""
    return LLMReply(
        content="",
        tool_calls=(
            ToolCall(id="call-s1", name="spawn_subagent", arguments=SPAWN_ARGS),
        ),
    )


async def test_execute_keeps_spawn_ack_alongside_pending_write() -> None:
    toolkit = _MixedToolkit()
    fake = FakeLLM([_mixed_call(), "已入队"])
    node = make_execute_node(fake, lambda account: toolkit)

    update = await node(_state(), _config())

    # 危险写在等确认,但同回合已入队的子代理回执必须一起带出去
    assert [item["name"] for item in update["pending_writes"]] == ["external_publish"]
    assert update[PENDING_KEY] == [{"child_run_id": "child-1", "title": "调研竞品"}]
    assert toolkit.executed == ["spawn_subagent"]


async def test_tool_exec_collects_spawn_ack_from_confirmed_call() -> None:
    toolkit = _MixedToolkit()
    node = make_tool_exec_node(lambda account: toolkit)
    pending = [{"id": "call-s1", "name": "spawn_subagent", "arguments": SPAWN_ARGS}]

    update = await node(_state(pending_writes=pending, writes_approved=True), _config())

    assert toolkit.executed == ["spawn_subagent"]
    assert update["pending_writes"] == []
    assert update[PENDING_KEY] == [{"child_run_id": "child-1", "title": "调研竞品"}]


async def test_tool_exec_merges_existing_spawn_ack() -> None:
    toolkit = _MixedToolkit()
    node = make_tool_exec_node(lambda account: toolkit)
    pending = [{"id": "call-s1", "name": "spawn_subagent", "arguments": SPAWN_ARGS}]
    early = [{"child_run_id": "child-0", "title": "上一批调研"}]

    update = await node(
        _state(pending_writes=pending, pending_subagents=early, writes_approved=True),
        _config(),
    )

    # 同回合 execute 已入队的那条不能因为走确认闸被覆盖
    assert update[PENDING_KEY] == [
        {"child_run_id": "child-0", "title": "上一批调研"},
        {"child_run_id": "child-1", "title": "调研竞品"},
    ]


def test_route_after_tool_exec_waits_only_with_pending_children() -> None:
    assert route_after_tool_exec({"pending_subagents": [{"child_run_id": "c"}]}) == (
        "subagent_wait"
    )
    assert route_after_tool_exec({"pending_subagents": []}) == "execute"
    assert route_after_tool_exec({}) == "execute"


def test_route_after_confirm_keeps_waiting_on_reject() -> None:
    assert route_after_confirm({"writes_approved": True}) == "tool_exec"
    assert (
        route_after_confirm(
            {"writes_approved": False, "pending_subagents": [{"child_run_id": "c"}]}
        )
        == "subagent_wait"
    )
    assert route_after_confirm({"writes_approved": False}) == "verify"


class _RoutingLLM(FakeLLM):
    """父 run 按脚本吐回复,子 run(goal 带 CHILD_MARK)固定回一份计划。

    子 run 的 plan job 与父 run 并发跑在同一个 worker 上,会共享脚本;用标记
    区分两边的调用,子 run 不推进父脚本的索引。
    """

    def __init__(self, responses: list[Any]) -> None:
        super().__init__(responses)
        self.child_calls: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: Any = None,
        response_format: Any = None,
    ) -> LLMReply:
        if any(CHILD_MARK in str(message.get("content") or "") for message in messages):
            self.child_calls.append(messages)
            return LLMReply(content="# 子计划\n1. 收集三家定价")
        return await super().complete(
            messages, model=model, tools=tools, response_format=response_format
        )


def _wait_status(
    client: TestClient, run_id: str, expected: str, *, deadline: float = 20.0
) -> dict:
    """轮询 run 状态(后台 worker 推进,同步响应靠不住)。"""
    end = time.monotonic() + deadline
    run: dict = {}
    while time.monotonic() < end:
        run = client.get(f"/api/v1/runs/{run_id}", headers=ACCOUNT).json()
        if run["status"] == expected:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run 未到 {expected}: {run}")


def _subagents(client: TestClient, run_id: str) -> list[dict]:
    resp = client.get(f"/api/v1/runs/{run_id}/subagents", headers=ACCOUNT)
    assert resp.status_code == 200
    return resp.json()


def _app(
    tmp_path: Path,
    name: str,
    fake: FakeLLM,
    danger: FakeDangerTool | None,
) -> Any:
    settings = _settings(
        tmp_path,
        name,
        llm_enabled=True,
        tools_enabled=True,
        jobs_enabled=True,
        jobs_poll_interval_seconds=0.01,
        approval_timeout_seconds=0,
        confirmation_timeout_seconds=0,
        question_timeout_seconds=0,
        run_timeout_seconds=0,
        run_recovery_sweep_interval_seconds=0.0,
        subagent_wait_sweep_interval_seconds=0.0,
        subagent_enabled=True,
        plan_auto_approve=False,
    )
    holder: dict[str, Any] = {}

    async def extras(account_id: str) -> list[Any]:
        return [
            build_spawn_tool(
                holder["session"], settings=settings, run_graph=holder["graph"]
            )
        ]

    def toolkit_factory(account: str) -> Any:
        base = build_tools(account)
        return base.extended([danger.spec()]) if danger is not None else base

    app = create_app(
        settings,
        llm_client=fake,
        toolkit_factory=toolkit_factory,
        tool_extras_factory=extras,
        token_verifier=_IDP.verifier(),
    )
    return app, holder


def test_confirmed_write_and_spawn_in_same_turn_still_waits(tmp_path: Path) -> None:
    """① 同回合:危险写等确认、spawn 已入队 → 确认后进等待节点而不是直接收尾。"""
    danger = FakeDangerTool()
    fake = _RoutingLLM(
        [
            "# 计划",
            _mixed_call(),
            "已发布对外发布说明",
            VERIFY_PASS,
            "# 交付物",
        ]
    )
    app, holder = _app(tmp_path, "mixed.db", fake, danger)
    with TestClient(app) as client:
        holder["session"] = app.state.session_factory
        holder["graph"] = app.state.run_graph
        run_id = _create_run(client)

        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        _wait_status(client, run_id, "awaiting_approval")
        client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        _wait_status(client, run_id, "awaiting_confirmation")
        # 写操作还没执行,子代理已在 execute 轮入队
        assert danger.sent == []
        assert [child["title"] for child in _subagents(client, run_id)] == ["调研竞品"]

        client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve"},
            headers=ACCOUNT,
        )
        _wait_status(client, run_id, "awaiting_subagent")

        assert danger.sent == [{"title": "对外发布说明"}]
        children = _subagents(client, run_id)
        assert [child["title"] for child in children] == ["调研竞品"]
        # 子 run 停在 plan 审批(plan_auto_approve=False),父 run 不会提前继续
        assert children[0]["status"] != "completed"


def test_spawn_under_confirm_policy_waits_after_approval(tmp_path: Path) -> None:
    """② spawn 被账号策略设成 confirm:确认前不执行,确认后进等待节点。"""
    fake = _RoutingLLM(["# 计划", _spawn_call(), "已入队,等待子代理"])
    app, holder = _app(tmp_path, "spawn-confirm.db", fake, None)
    with TestClient(app) as client:
        holder["session"] = app.state.session_factory
        holder["graph"] = app.state.run_graph
        resp = client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "spawn_subagent", "action": "confirm"}]},
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        run_id = _create_run(client)

        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        _wait_status(client, run_id, "awaiting_approval")
        client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        _wait_status(client, run_id, "awaiting_confirmation")
        # 确认前没有子 run:spawn 只是候选写操作
        assert _subagents(client, run_id) == []

        client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve"},
            headers=ACCOUNT,
        )
        _wait_status(client, run_id, "awaiting_subagent")

        assert [child["title"] for child in _subagents(client, run_id)] == ["调研竞品"]
