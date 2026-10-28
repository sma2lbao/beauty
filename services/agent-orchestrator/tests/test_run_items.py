"""过程项(P1-D 事件分类学)采集、投影与 API 测试。

覆盖契约 docs/design/event-taxonomy.md 的关键面:

- ``classify_tool_kind`` 分类规则与标题/data 组装(纯函数);
- ``ItemProjector`` 把 started + completed 合并为一个过程项(sink 快照);
- ``GET /api/v1/runs/{id}/items`` 空数组不 404、按 seq 升序投影、data 浅合并;
- ``GET /api/v1/runs/{id}/events`` 回放包含 ``run.item.v1``;
- 真跑一次 run(替身 LLM + 替身工具面)产生 plan / todo / file_change / tool
  过程项,thread/turn/step_id 正确;
- plan 版本化:生成第 1 版后编辑计划落第 2 版,supersedes 指向第 1 版。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from common.service_testkit import app_client
from database.session import session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.graph import items
from agent_orchestrator.graph.items import (
    KIND_FILE_CHANGE,
    KIND_MCP_CALL,
    KIND_PLAN,
    KIND_SEARCH,
    KIND_TODO,
    KIND_TOOL,
    STATUS_COMPLETED,
    classify_tool_kind,
    tool_item_data,
    tool_item_title,
)
from agent_orchestrator.main import create_app
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services.items import MAX_PLAN_CHARS, plan_content
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.todos import build_todo_tool
from agent_orchestrator.services.tools import ToolRisk, ToolSpec, build_tools
from tests.conftest import (
    TENANT,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _settings,
    verifier,
)

# ---- 纯函数:分类 / 标题 / data ----


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("todo_write", KIND_TODO),
        ("mcp__alpha__echo", KIND_MCP_CALL),
        ("write", KIND_FILE_CHANGE),
        ("edit", KIND_FILE_CHANGE),
        ("fetch", KIND_SEARCH),
        ("search_knowledge", KIND_SEARCH),
        ("read", KIND_TOOL),
        ("exec", KIND_TOOL),
        ("ask_question", KIND_TOOL),
    ],
)
def test_classify_tool_kind(name: str, kind: str) -> None:
    assert classify_tool_kind(name) == kind


def test_tool_item_title_and_data() -> None:
    assert tool_item_title(KIND_TODO, "todo_write", "{}") == "更新任务清单"
    assert (
        tool_item_title(KIND_FILE_CHANGE, "write", '{"path": "src/a.ts"}')
        == "写入 src/a.ts"
    )
    assert (
        tool_item_title(KIND_FILE_CHANGE, "edit", '{"file_path": "src/b.ts"}')
        == "编辑 src/b.ts"
    )
    assert (
        tool_item_title(KIND_SEARCH, "search_knowledge", '{"query": "fastapi"}')
        == "检索:fastapi"
    )
    assert tool_item_title(KIND_SEARCH, "fetch", '{"url": "https://x.dev"}') == (
        "检索:https://x.dev"
    )
    assert tool_item_title(KIND_MCP_CALL, "mcp__alpha__echo", "{}") == "MCP:echo"
    assert tool_item_title(KIND_TOOL, "read", "{}") == "调用 read"
    # 参数不是合法 JSON:标题降级,不抛错
    assert tool_item_title(KIND_TOOL, "read", "not-json") == "调用 read"

    started = tool_item_data(KIND_TOOL, "read", '{"path": "src/a.ts"}')
    assert started == {"name": "read", "arguments": '{"path": "src/a.ts"}'}
    completed = tool_item_data(
        KIND_MCP_CALL,
        "mcp__alpha__echo",
        "{}",
        {"ok": True, "detail": "pong"},
    )
    assert completed["ok"] is True
    assert completed["server"] == "alpha"
    assert completed["tool"] == "echo"


def test_item_projector_merges_started_and_completed() -> None:
    records: list[items.ItemSpan] = []

    class _Sink:
        def push(self, span: items.ItemSpan) -> None:
            records.append(span)

    projector = items.ItemProjector(_Sink(), step_id=lambda: "step-1")
    span = projector.begin(
        KIND_TOOL, "调用 read", node="execute", data={"name": "read"}
    )
    assert span is not None
    assert span.phase == items.PHASE_STARTED
    assert span.status is None
    assert span.step_id == "step-1"
    assert span.duration_ms is None

    projector.finish(span, data={"ok": True})
    assert span.phase == items.PHASE_COMPLETED
    assert span.status == STATUS_COMPLETED
    assert span.step_id == "step-1"
    assert span.finished_at is not None
    assert span.duration_ms is not None
    assert len(records) == 2

    # 终态单项记录(策略拒绝等未执行的调用):只上报一次 completed/denied
    projector.record(
        KIND_TODO,
        "更新任务清单",
        status=items.STATUS_DENIED,
        data={"ok": False},
        node="execute",
    )
    assert records[-1].phase == items.PHASE_COMPLETED
    assert records[-1].status == items.STATUS_DENIED


def test_item_projector_without_sink_is_noop() -> None:
    projector = items.ItemProjector()
    assert projector.active() is False
    assert projector.current_step_id() is None
    assert projector.begin(KIND_TOOL, "调用 read") is None
    projector.finish(None)
    projector.record(KIND_TOOL, "调用 read")


def test_plan_content_truncates_at_limit() -> None:
    content, chars, truncated = plan_content("x" * (MAX_PLAN_CHARS + 1))
    assert truncated is True
    assert chars == MAX_PLAN_CHARS
    assert len(content) == MAX_PLAN_CHARS

    content, chars, truncated = plan_content("短计划")
    assert (content, chars, truncated) == ("短计划", 3, False)


# ---- API:空数组 / 回放 / 投影合并 ----


def _append_items(
    client: TestClient, run_id: str, payloads: list[dict[str, Any]]
) -> None:
    """直插 run.item.v1 事件(绕过图,验证投影合并语义)。"""

    async def _run() -> None:
        async with session_scope(client.app.state.session_factory) as session:
            run = await session.get(Run, run_id)
            assert run is not None
            for payload in payloads:
                await append_event(
                    session, run, "run.item.v1", payload, record_outbox=False
                )

    asyncio.run(_run())


def _item_payload(item_id: str, phase: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_id": item_id,
        "thread_id": "run:root",
        "turn_id": "root",
        "kind": KIND_TOOL,
        "phase": phase,
        "node": "execute",
        "step_id": None,
        "attempt": 1,
        "title": "调用 read",
        "data": {},
        "started_at": "2026-10-27T00:00:00+00:00",
    }
    payload.update(extra)
    return payload


def test_items_endpoint_returns_empty_list(client: TestClient) -> None:
    run_id = _create_run(client)

    resp = client.get(f"/api/v1/runs/{run_id}/items", headers=TENANT)

    assert resp.status_code == 200
    assert resp.json() == []


def test_items_projection_merges_phases(client: TestClient) -> None:
    run_id = _create_run(client)
    item_id = f"{run_id}:1:tool:1"
    _append_items(
        client,
        run_id,
        [
            _item_payload(item_id, items.PHASE_STARTED, data={"name": "read"}),
            _item_payload(
                item_id,
                items.PHASE_COMPLETED,
                status=STATUS_COMPLETED,
                data={"ok": True},
                started_at="2026-10-27T00:00:00+00:00",
                finished_at="2026-10-27T00:00:01+00:00",
                duration_ms=1000,
            ),
        ],
    )

    items_body = client.get(f"/api/v1/runs/{run_id}/items", headers=TENANT).json()
    assert len(items_body) == 1
    projected = items_body[0]
    assert projected["item_id"] == item_id
    assert projected["phase"] == items.PHASE_COMPLETED
    assert projected["status"] == STATUS_COMPLETED
    assert projected["data"] == {"name": "read", "ok": True}
    assert projected["duration_ms"] == 1000
    assert projected["started_at"].startswith("2026-10-27T00:00:00")

    events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
    types = [event["type"] for event in events]
    assert types.count("run.item.v1") == 2
    # 回放可见 item 事件(step 事件仍然不回放)
    assert "run.step.v1" not in types


# ---- 真跑一次 run:plan / todo / file_change / tool ----


async def _write_file(args: dict[str, Any]) -> str:
    return json.dumps(
        {
            "ok": True,
            "path": args.get("path", "src/a.ts"),
            "sha256_before": "0" * 64,
            "sha256_after": "1" * 64,
        }
    )


async def _read_file(args: dict[str, Any]) -> str:
    return json.dumps(
        {"ok": True, "path": args.get("path", "src/a.ts"), "content": "hello"}
    )


WRITE_TOOL = ToolSpec(
    name="write",
    description="写文件(测试替身)",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    handler=_write_file,
    risk=ToolRisk.WRITE,
)

READ_TOOL = ToolSpec(
    name="read",
    description="读文件(测试替身)",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    handler=_read_file,
    risk=ToolRisk.READ,
)

PLAN_TEXT = "## 计划\n1. 整理本周日程\n2. 生成周报"
DELIVER_TEXT = "# 交付\n本周日程已整理"


def _item_run_client(tmp_path: Path) -> tuple[TestClient, Any]:
    """装配带 todo/write/read 工具面的 app(图内真实执行工具)。"""

    fake = FakeLLM(
        [
            PLAN_TEXT,
            LLMReply(
                content="",
                tool_calls=[
                    ToolCall(
                        id="c-todo",
                        name="todo_write",
                        arguments=json.dumps(
                            {
                                "todos": [
                                    {
                                        "content": "整理本周日程",
                                        "status": "in_progress",
                                    }
                                ]
                            }
                        ),
                    ),
                    ToolCall(
                        id="c-write",
                        name="write",
                        arguments=json.dumps({"path": "src/a.ts", "content": "hi"}),
                    ),
                    ToolCall(
                        id="c-read",
                        name="read",
                        arguments=json.dumps({"path": "src/a.ts"}),
                    ),
                ],
            ),
            LLMReply(content="整理完成"),
            '{"summary": "通过", "verdicts": {"日程已整理": true}}',
            DELIVER_TEXT,
        ]
    )
    holder: dict[str, Any] = {}

    def _factory(tenant: str):
        extras: list[ToolSpec] = [WRITE_TOOL, READ_TOOL]
        session_factory = holder.get("session_factory")
        if session_factory is not None:
            extras.append(build_todo_tool(session_factory))
        return build_tools(tenant, knowledge=FakeKnowledge()).extended(extras)

    app = create_app(
        _settings(tmp_path, "items.db", llm_enabled=True, tools_enabled=True),
        llm_client=fake,
        toolkit_factory=_factory,
        token_verifier=verifier(),
    )
    return app, holder


def test_full_run_emits_plan_todo_file_change_and_tool_items(
    tmp_path: Path,
) -> None:
    app, holder = _item_run_client(tmp_path)
    with app_client(app) as client:
        holder["session_factory"] = client.app.state.session_factory
        run_id = _create_run(client)

        planned = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        assert planned.status_code == 200
        assert planned.json()["status"] == "awaiting_approval"

        approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
        assert approved.status_code == 200
        assert approved.json()["status"] == "completed"

        body = client.get(f"/api/v1/runs/{run_id}/items", headers=TENANT).json()

    kinds = [item["kind"] for item in body]
    assert kinds == [KIND_PLAN, KIND_TODO, KIND_FILE_CHANGE, KIND_TOOL]

    by_kind = {item["kind"]: item for item in body}
    for item in body:
        assert item["thread_id"] == f"run:{run_id}"
        assert item["turn_id"] == run_id
        assert item["attempt"] == 1
        assert item["phase"] == items.PHASE_COMPLETED
        assert item["status"] == STATUS_COMPLETED
        assert item["finished_at"] is not None
        assert item["duration_ms"] >= 0

    plan = by_kind[KIND_PLAN]
    assert plan["title"] == "计划(第 1 版)"
    assert plan["node"] == "plan"
    assert plan["step_id"] is not None
    assert plan["data"]["version"] == 1
    assert plan["data"]["supersedes"] is None
    assert plan["data"]["content"] == PLAN_TEXT
    assert plan["data"]["chars"] == len(PLAN_TEXT)
    assert plan["data"]["truncated"] is False

    todo = by_kind[KIND_TODO]
    assert todo["node"] is None
    assert todo["step_id"] is None
    assert todo["title"] == "更新任务清单"
    assert todo["data"]["revision"] == 1
    assert todo["data"]["todos"] == [
        {"content": "整理本周日程", "status": "in_progress"}
    ]
    assert todo["data"]["counts"]["in_progress"] == 1

    file_change = by_kind[KIND_FILE_CHANGE]
    assert file_change["title"] == "写入 src/a.ts"
    assert file_change["node"] == "execute"
    assert file_change["step_id"] is not None
    assert file_change["data"]["name"] == "write"
    assert file_change["data"]["ok"] is True
    assert file_change["data"]["paths"] == ["src/a.ts"]
    assert file_change["data"]["sha256_after"] == "1" * 64

    tool = by_kind[KIND_TOOL]
    assert tool["title"] == "调用 read"
    assert tool["node"] == "execute"
    assert tool["data"]["name"] == "read"
    assert tool["data"]["ok"] is True

    # 回放事件里能看到过程项(step 事件仍隐藏)
    events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
    types = [event["type"] for event in events]
    assert "run.item.v1" in types
    assert "run.step.v1" not in types
    item_payloads = [
        event["payload"] for event in events if event["type"] == "run.item.v1"
    ]
    assert {payload["kind"] for payload in item_payloads} == set(kinds)


# ---- plan 版本化 ----


def test_plan_version_supersedes_previous(client: TestClient) -> None:
    run_id = _create_run(client)

    assert client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).status_code == 200
    assert (
        client.patch(
            f"/api/v1/runs/{run_id}/plan",
            json={"plan_markdown": "# 第二版计划"},
            headers=TENANT,
        ).status_code
        == 200
    )

    body = client.get(f"/api/v1/runs/{run_id}/items", headers=TENANT).json()
    plans = [item for item in body if item["kind"] == KIND_PLAN]
    assert len(plans) == 2

    first, second = plans
    assert first["data"]["version"] == 1
    assert first["data"]["supersedes"] is None
    assert second["data"]["version"] == 2
    assert second["data"]["supersedes"] == first["item_id"]
    assert second["data"]["content"] == "# 第二版计划"
    assert second["data"]["chars"] == len("# 第二版计划")
    assert second["data"]["truncated"] is False
    # 新版本是编辑产物:没有步骤锚点,node 为空
    assert second["node"] is None
    assert second["step_id"] is None
