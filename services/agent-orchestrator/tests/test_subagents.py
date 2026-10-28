"""子代理委派 P1-E 单测:工具参数、上限、继承、完整 spawn、取消/超时与投影。

约定:临时 sqlite 文件库 + 真实 Run 行;子 run 的执行用桩图(只实现
``ainvoke``)——本文件验证的是 spawn 的编排语义(建行/事件/轮询收尾/用量),
真实图行为由既有 run 测试覆盖,零网络。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.container_assemblies import ContainerAssemblyMixin
from agent_orchestrator.graph import items
from agent_orchestrator.models.runs import PARENT_KIND_SUBAGENT, Run, RunEvent
from agent_orchestrator.services import subagents, usage_events
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.items import append_item_event, resolve_thread_id
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_quota import _usage_boundary
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import _create_run, _settings, bearer

TENANT = "t-1"
OTHER = "t-2"

PARAMS = {
    "title": "调研竞品",
    "task": "整理三家的定价页差异",
    "agent_name": "调研员",
    "instructions": "只做调研,不改代码",
}


class _StubGraph:
    """桩图:只实现 ``ainvoke``,按用例给终态/中断/挂起。"""

    def __init__(
        self,
        *,
        final_status: str = "completed",
        results: tuple[str, ...] = ("调研完成",),
        status_reason: str | None = None,
        interrupt: bool = False,
        artifact_content: str = "调研完成",
    ) -> None:
        self.final_status = final_status
        self.results = list(results)
        self.status_reason = status_reason
        self.interrupt = interrupt
        self.artifact_content = artifact_content

    async def ainvoke(
        self, state: dict[str, Any], config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.interrupt:
            return {"plan_markdown": "# 计划", "__interrupt__": [{"kind": "plan"}]}
        values: dict[str, Any] = {
            "plan_markdown": "# 计划",
            "rounds": 1,
            "final_status": self.final_status,
            "results": self.results,
            # 真实图在交付阶段必给 artifact:摘要优先取报告正文(§6)
            "artifact": {
                "kind": "markdown",
                "title": "子代理报告",
                "content": self.artifact_content,
            },
        }
        if self.status_reason is not None:
            values["status_reason"] = self.status_reason
        return values


class _HangingGraph:
    """永不返回的桩图:用于超时/取消路径(任务被 cancel 后即结束)。"""

    async def ainvoke(
        self, state: dict[str, Any], config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {}


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose。"""

    settings = _settings(tmp_path, "subagents.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


@pytest.fixture
def settings(tmp_path: Path):
    return _settings(tmp_path, "subagents.db")


async def _create_parent(
    factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str = TENANT,
    parent_kind: str | None = None,
    parent_run_id: str | None = None,
    **kwargs: Any,
) -> Run:
    async with session_scope(factory) as session:
        run = Run(
            tenant_id=tenant_id,
            title="主任务",
            goal="把竞品资料整理成表格",
            parent_kind=parent_kind,
            parent_run_id=parent_run_id,
            **kwargs,
        )
        session.add(run)
        await session.flush()
        return run


async def _spawn(
    factory: async_sessionmaker[AsyncSession],
    settings: Any,
    parent: Run,
    *,
    params: dict[str, Any] | None = None,
    graph: Any = None,
    tenant_id: str = TENANT,
):
    async with session_scope(factory) as session:
        return await subagents.spawn_subagent(
            session,
            parent=parent,
            params=dict(PARAMS if params is None else params),
            settings=settings,
            factory=factory,
            run_graph=graph if graph is not None else _StubGraph(),
            store=None,
            workspace=None,
            downloader=None,
            context=None,
        )


async def _children(
    factory: async_sessionmaker[AsyncSession], parent_id: str
) -> list[Run]:
    async with session_scope(factory) as session:
        rows = await session.scalars(
            select(Run)
            .where(Run.parent_run_id == parent_id)
            .order_by(Run.created_at, Run.id)
        )
        return list(rows.all())


async def _events(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[RunEvent]:
    async with session_scope(factory) as session:
        rows = await session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        )
        return list(rows.all())


async def _item_payloads(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[dict[str, Any]]:
    return [
        event.payload
        for event in await _events(factory, run_id)
        if event.type == items.EVENT_TYPE
    ]


# ---- 工具面 ----


def test_spec_is_write_risk_and_declares_limits(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    tool = subagents.build_spawn_tool(factory, settings=settings)
    assert tool.name == subagents.SPAWN_TOOL_NAME
    # 建 run/写事件:WRITE 级;不进人工确认闸
    assert tool.risk is ToolRisk.WRITE
    schema = tool.parameters
    assert schema["required"] == ["title", "task"]
    assert schema["properties"]["tools"]["maxItems"] == settings.subagent_max_tools
    assert schema["properties"]["timeout_seconds"]["minimum"] == 30
    assert schema["properties"]["timeout_seconds"]["maximum"] == 1800
    assert tool.description


async def test_tool_requires_run_context(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    tool = subagents.build_spawn_tool(factory, settings=settings)
    with bind_tool_context(ToolContext(tenant_id=TENANT)):
        payload = json.loads(await tool.handler(dict(PARAMS)))
    assert payload["ok"] is False
    assert payload["error"] == "subagent.invalid_arguments"


async def test_tool_rejects_other_tenant(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    tool = subagents.build_spawn_tool(factory, settings=settings)
    parent = await _create_parent(factory)
    with bind_tool_context(ToolContext(tenant_id=OTHER, run_id=parent.id)):
        payload = json.loads(await tool.handler(dict(PARAMS)))
    assert payload["ok"] is False
    assert await _children(factory, parent.id) == []


class _StubContainer(ContainerAssemblyMixin):
    """只带 ``_load_extras`` 需要的属性的容器桩(其余依赖 getattr 兜底)。"""

    def __init__(
        self, settings: Any, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.settings = settings
        self.session_factory = factory
        self.skill_runner = None
        self.mcp_stdio_pool = None
        self.context_policy = None
        self.tool_offloader = None


async def _extra_names(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> set[str]:
    extras = await _StubContainer(settings, factory)._extras_factory()(TENANT)
    return {spec.name for spec in extras}


async def test_spawn_tool_mounted_only_in_top_level_run(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    child = await _create_parent(
        factory,
        parent_kind=PARENT_KIND_SUBAGENT,
        parent_run_id=parent.id,
    )
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=parent.id)):
        assert subagents.SPAWN_TOOL_NAME in await _extra_names(factory, settings)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=child.id)):
        # 子代理不能再派生(深度上限 1 在工具面就掐断)
        assert subagents.SPAWN_TOOL_NAME not in await _extra_names(factory, settings)
    with bind_tool_context(ToolContext(tenant_id=TENANT)):
        assert subagents.SPAWN_TOOL_NAME not in await _extra_names(factory, settings)


async def test_spawn_tool_honours_switch(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    disabled = settings.model_copy(update={"subagent_enabled": False})
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=parent.id)):
        assert subagents.SPAWN_TOOL_NAME not in await _extra_names(factory, disabled)


# ---- 参数校验 ----


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"task": "x"}, "subagent.invalid_arguments"),
        ({"title": "  ", "task": "x"}, "subagent.invalid_arguments"),
        ({"title": "x" * 121, "task": "x"}, "subagent.invalid_arguments"),
        ({"title": "t", "task": "x" * 4001}, "subagent.invalid_arguments"),
        (
            {"title": "t", "task": "x", "agent_name": "n" * 41},
            "subagent.invalid_arguments",
        ),
        ({"title": "t", "task": "x", "agent_name": 3}, "subagent.invalid_arguments"),
        ({"title": "t", "task": "x", "instructions": 3}, "subagent.invalid_arguments"),
        (
            {"title": "t", "task": "x", "instructions": "i" * 4001},
            "subagent.invalid_arguments",
        ),
        ({"title": "t", "task": "x", "tools": "read"}, "subagent.invalid_arguments"),
        ({"title": "t", "task": "x", "tools": [3]}, "subagent.invalid_arguments"),
        (
            {"title": "t", "task": "x", "timeout_seconds": 29},
            "subagent.invalid_arguments",
        ),
        (
            {"title": "t", "task": "x", "timeout_seconds": 1801},
            "subagent.invalid_arguments",
        ),
        (
            {"title": "t", "task": "x", "timeout_seconds": "600"},
            "subagent.invalid_arguments",
        ),
    ],
)
async def test_invalid_arguments_do_not_create_child(
    factory: async_sessionmaker[AsyncSession],
    settings: Any,
    params: dict[str, Any],
    code: str,
) -> None:
    parent = await _create_parent(factory)
    payload = await _spawn(factory, settings, parent, params=params)
    assert payload["ok"] is False
    assert payload["error"] == code
    assert await _children(factory, parent.id) == []
    assert await _item_payloads(factory, parent.id) == []


async def test_too_many_tools_rejected(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    params = {
        **PARAMS,
        "tools": [f"tool-{i}" for i in range(settings.subagent_max_tools + 1)],
    }
    payload = await _spawn(factory, settings, parent, params=params)
    assert payload["error"] == "subagent.invalid_arguments"
    assert await _children(factory, parent.id) == []


# ---- 上限与工具面继承 ----


async def test_limit_exceeded_after_quota(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    limited = settings.model_copy(update={"subagent_max_per_run": 1})
    first = await _spawn(factory, limited, parent)
    assert first["ok"] is True
    second = await _spawn(factory, limited, parent)
    assert second["ok"] is False
    assert second["error"] == "subagent.limit_exceeded"
    assert len(await _children(factory, parent.id)) == 1


async def test_depth_exceeded_for_subagent(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    top = await _create_parent(factory)
    child = await _create_parent(
        factory, parent_kind=PARENT_KIND_SUBAGENT, parent_run_id=top.id
    )
    payload = await _spawn(factory, settings, child)
    assert payload["ok"] is False
    assert payload["error"] == "subagent.depth_exceeded"


async def test_tools_must_be_subset_of_parent_allowlist(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory, skill_tools=["read"])
    payload = await _spawn(
        factory, settings, parent, params={**PARAMS, "tools": ["read", "fetch"]}
    )
    assert payload["error"] == "subagent.tools_not_subset"
    assert await _children(factory, parent.id) == []


async def test_tools_without_parent_allowlist_only_length_checked(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    payload = await _spawn(
        factory, settings, parent, params={**PARAMS, "tools": ["read", "fetch"]}
    )
    assert payload["ok"] is True
    child = (await _children(factory, parent.id))[0]
    assert child.agent_tools == ["read", "fetch"]


async def test_tools_inherited_and_spawn_itself_stripped(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(
        factory, skill_tools=[subagents.SPAWN_TOOL_NAME, "read", "read"]
    )
    payload = await _spawn(factory, settings, parent)
    assert payload["ok"] is True
    child = (await _children(factory, parent.id))[0]
    # 父级有效白名单继承下来、去重、且不含 spawn 自身(防递归)
    assert child.agent_tools == ["read"]


# ---- 完整 spawn ----


async def test_completed_spawn_creates_child_and_items(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory, workspace_id="ws-1", mcp_server_ids=["m1"])
    payload = await _spawn(factory, settings, parent)

    assert payload["ok"] is True
    assert payload["status"] == "completed"
    assert payload["summary"] == "调研完成"
    assert payload["usage"] == {"total_tokens": 0, "calls": 0}

    children = await _children(factory, parent.id)
    assert len(children) == 1
    child = children[0]
    assert child.id == payload["subagent_run_id"]
    assert child.parent_run_id == parent.id
    assert child.parent_kind == PARENT_KIND_SUBAGENT
    assert child.title == PARAMS["title"]
    assert child.goal == PARAMS["task"]
    assert child.agent_name == PARAMS["agent_name"]
    assert child.agent_prompt == PARAMS["instructions"]
    # 继承 workspace/mcp,不继承父对话与技能快照
    assert child.workspace_id == "ws-1"
    assert child.mcp_server_ids == ["m1"]
    assert child.conversation_id is None
    assert child.skill_id is None
    assert child.status == "completed"

    child_events = await _events(factory, child.id)
    created = [event for event in child_events if event.type == "run.created.v1"]
    assert created[0].payload["parent_run_id"] == parent.id
    assert created[0].payload["parent_kind"] == PARENT_KIND_SUBAGENT
    assert created[0].payload["agent_name"] == PARAMS["agent_name"]
    assert "run.completed.v1" in {event.type for event in child_events}


async def test_parent_item_lifecycle_thread_and_turn(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    await _spawn(factory, settings, parent)
    payloads = await _item_payloads(factory, parent.id)
    subagent_payloads = [
        item for item in payloads if item["kind"] == items.KIND_SUBAGENT
    ]
    assert [item["phase"] for item in subagent_payloads] == ["started", "completed"]
    started, completed = subagent_payloads
    expected_id = f"{parent.id}:{parent.attempts}:subagent:1"
    assert started["item_id"] == expected_id
    assert completed["item_id"] == expected_id
    # 同父 thread / turn:控制台按 thread 聚合时子代理不另开分支
    assert started["thread_id"] == f"run:{parent.id}"
    assert started["turn_id"] == parent.id
    assert completed["turn_id"] == parent.id
    assert started["node"] == "execute"
    child_id = started["data"]["child_run_id"]
    assert started["data"]["agent_name"] == PARAMS["agent_name"]
    assert started["data"]["title"] == PARAMS["title"]
    assert started["data"]["tools"] is None
    assert started["data"]["timeout_seconds"] == 600
    assert completed["status"] == items.STATUS_COMPLETED
    assert completed["data"]["child_run_id"] == child_id
    assert completed["data"]["child_status"] == "completed"
    assert completed["data"]["summary"] == "调研完成"
    assert completed["data"]["usage"] == {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


async def test_second_spawn_continues_item_numbering(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    await _spawn(factory, settings, parent)
    await _spawn(factory, settings, parent, params={**PARAMS, "title": "第二个"})
    started = [
        item
        for item in await _item_payloads(factory, parent.id)
        if item["kind"] == items.KIND_SUBAGENT and item["phase"] == "started"
    ]
    assert [item["item_id"] for item in started] == [
        f"{parent.id}:{parent.attempts}:subagent:1",
        f"{parent.id}:{parent.attempts}:subagent:2",
    ]


async def test_failed_child_marks_parent_item_failed(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    payload = await _spawn(
        factory,
        settings,
        parent,
        graph=_StubGraph(final_status="failed", status_reason="验收未通过"),
    )
    assert payload["ok"] is False
    assert payload["error"] == "subagent.failed"
    assert payload["detail"] == "验收未通过"
    failed_items = [
        item
        for item in await _item_payloads(factory, parent.id)
        if item["kind"] == items.KIND_SUBAGENT and item["phase"] == items.PHASE_FAILED
    ]
    assert len(failed_items) == 1
    assert failed_items[0]["data"]["reason"] == "failed"
    assert failed_items[0]["status"] == items.STATUS_FAILED


async def test_awaiting_child_returns_immediately(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    payload = await _spawn(factory, settings, parent, graph=_StubGraph(interrupt=True))
    assert payload["ok"] is True
    assert payload["status"] == "awaiting_approval"
    assert payload["summary"] == ""
    assert "人工审批" in payload["note"]
    child = (await _children(factory, parent.id))[0]
    assert child.status == "awaiting_approval"
    completed = [
        item
        for item in await _item_payloads(factory, parent.id)
        if item["phase"] == items.PHASE_COMPLETED
    ]
    assert completed[0]["data"]["child_status"] == "awaiting_approval"


async def test_summary_is_truncated(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    short = settings.model_copy(update={"subagent_summary_max_chars": 4})
    payload = await _spawn(
        factory,
        short,
        parent,
        graph=_StubGraph(results=("一二三四五六",), artifact_content="一二三四五六"),
    )
    assert payload["summary"] == "一二三四"


# ---- 超时与取消 ----


async def test_timeout_cancels_child_and_marks_reason(
    factory: async_sessionmaker[AsyncSession],
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _timeout(*args: Any, **kwargs: Any) -> str:
        return subagents._ABORT_TIMEOUT

    monkeypatch.setattr(subagents, "_wait_child", _timeout)
    parent = await _create_parent(factory)
    payload = await _spawn(factory, settings, parent, graph=_HangingGraph())
    assert payload["ok"] is False
    assert payload["error"] == "subagent.timeout"
    child = (await _children(factory, parent.id))[0]
    assert child.status == "cancelled"
    assert child.status_reason == subagents.TIMEOUT_REASON
    failed = [
        item
        for item in await _item_payloads(factory, parent.id)
        if item["phase"] == items.PHASE_FAILED
    ]
    assert failed[0]["data"]["reason"] == "timeout"
    assert failed[0]["data"]["child_status"] == "cancelled"


async def test_child_cancelled_externally_reports_cancelled(
    factory: async_sessionmaker[AsyncSession],
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def _cancelled(
        factory_: Any,
        *,
        parent_id: str,
        child_id: str,
        task: Any,
        timeout_seconds: float,
    ) -> str:
        captured["child_id"] = child_id
        await subagents._cancel_child(factory_, child_id, "已取消")
        return subagents._ABORT_CANCELLED

    monkeypatch.setattr(subagents, "_wait_child", _cancelled)
    parent = await _create_parent(factory)
    payload = await _spawn(factory, settings, parent, graph=_HangingGraph())
    assert payload["ok"] is False
    assert payload["error"] == "subagent.cancelled"
    child = (await _children(factory, parent.id))[0]
    assert child.status == "cancelled"
    failed = [
        item
        for item in await _item_payloads(factory, parent.id)
        if item["phase"] == items.PHASE_FAILED
    ]
    assert failed[0]["data"]["reason"] == "cancelled"


async def test_parent_cancel_cascades_to_child(
    factory: async_sessionmaker[AsyncSession],
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _parent_cancelled(*args: Any, **kwargs: Any) -> str:
        return subagents._ABORT_PARENT

    monkeypatch.setattr(subagents, "_wait_child", _parent_cancelled)
    parent = await _create_parent(factory)
    payload = await _spawn(factory, settings, parent, graph=_HangingGraph())
    assert payload["ok"] is False
    child = (await _children(factory, parent.id))[0]
    assert child.status == "cancelled"
    assert child.status_reason == subagents.PARENT_CANCELLED_REASON
    events = await _events(factory, child.id)
    assert "run.cancelled.v1" in {event.type for event in events}


async def test_wait_child_returns_timeout_when_deadline_passes(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    task: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(3600))
    try:
        abort = await subagents._wait_child(
            factory,
            parent_id="00000000000000000000000000000000",
            child_id="00000000000000000000000000000000",
            task=task,
            timeout_seconds=0.05,
        )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert abort == subagents._ABORT_TIMEOUT


# ---- 列表与用量投影 ----


async def test_list_subagents_empty_is_not_404(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _create_parent(factory)
    async with session_scope(factory) as session:
        assert await subagents.list_subagents(session, TENANT, parent.id) == []


async def test_list_subagents_requires_run(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _create_parent(factory)
    async with session_scope(factory) as session:
        with pytest.raises(Exception) as exc:
            await subagents.list_subagents(session, OTHER, parent.id)
    assert getattr(exc.value, "code", "") == "run.not_found"


async def test_list_subagents_returns_summary_and_usage(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    await _spawn(factory, settings, parent)
    async with session_scope(factory) as session:
        entries = await subagents.list_subagents(session, TENANT, parent.id)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.parent_run_id == parent.id
    assert entry.title == PARAMS["title"]
    assert entry.agent_name == PARAMS["agent_name"]
    assert entry.status.value == "completed"
    assert entry.summary == "调研完成"
    assert entry.usage.total_tokens == 0


async def test_usage_event_recorded_at_boundary(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _create_parent(factory)
    async with session_scope(factory) as session:
        run = await session.get(Run, parent.id)
        assert run is not None
        async with _usage_boundary(session, run):
            usage_service.record(
                usage_service.UsageSample(
                    model="deepseek-v3",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                )
            )
    events = [
        e
        for e in await _events(factory, parent.id)
        if e.type == usage_events.USAGE_EVENT
    ]
    assert len(events) == 1
    assert events[0].payload["calls"] == 1
    assert events[0].payload["total_tokens"] == 15
    assert events[0].payload["models"]["deepseek-v3"]["total_tokens"] == 15


async def test_usage_totals_aggregate_and_by_model(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _create_parent(factory)
    async with session_scope(factory) as session:
        run = await session.get(Run, parent.id)
        assert run is not None
        for model, tokens in (("m-1", 10), ("m-2", 20), ("m-1", 5)):
            sample = usage_service.UsageSample(
                model=model,
                prompt_tokens=tokens,
                completion_tokens=0,
                total_tokens=tokens,
            )
            await append_event(
                session,
                run,
                usage_events.USAGE_EVENT,
                usage_events.usage_payload([sample]),
                record_outbox=False,
            )
    async with session_scope(factory) as session:
        read = await usage_events.run_usage(session, TENANT, parent.id)
    assert read.run_id == parent.id
    assert read.calls == 3
    assert read.total_tokens == 35
    by_model = {entry.model: entry.total_tokens for entry in read.by_model}
    assert by_model == {"m-1": 15, "m-2": 20}


# ---- API ----


def test_api_subagents_and_usage_endpoints(client) -> None:
    run_id = _create_run(client)
    subagents_resp = client.get(
        f"/api/v1/runs/{run_id}/subagents", headers=bearer(TENANT)
    )
    assert subagents_resp.status_code == 200
    assert subagents_resp.json() == []

    usage_resp = client.get(f"/api/v1/runs/{run_id}/usage", headers=bearer(TENANT))
    assert usage_resp.status_code == 200
    assert usage_resp.json() == {
        "run_id": run_id,
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "by_model": [],
    }

    other = client.get(f"/api/v1/runs/{run_id}/subagents", headers=bearer(OTHER))
    assert other.status_code == 404


def test_run_read_exposes_parent_fields(client) -> None:
    run_id = _create_run(client)
    body = client.get(f"/api/v1/runs/{run_id}", headers=bearer(TENANT)).json()
    assert body["parent_kind"] is None
    assert body["agent_name"] is None


async def test_thread_id_falls_back_to_root_run(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    top = await _create_parent(factory)
    async with session_scope(factory) as session:
        child = Run(
            tenant_id=TENANT,
            title="子",
            goal="g",
            parent_run_id=top.id,
            parent_kind=PARENT_KIND_SUBAGENT,
        )
        session.add(child)
        await session.flush()
        assert await resolve_thread_id(session, child) == f"run:{top.id}"
        # 父 item 的 thread 与子 run 自查一致(同一 thread 聚合)
        assert await resolve_thread_id(session, top) == f"run:{top.id}"


async def test_item_append_matches_service_ids(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _create_parent(factory)
    async with session_scope(factory) as session:
        thread_id = await resolve_thread_id(session, parent)
        await append_item_event(
            session,
            parent,
            thread_id=thread_id,
            item_id=f"{parent.id}:1:subagent:9",
            kind=items.KIND_SUBAGENT,
            phase=items.PHASE_COMPLETED,
            title="手工项",
            data={"child_run_id": "c1", "summary": "摘要"},
            node="execute",
            status=items.STATUS_COMPLETED,
        )
    payloads = await _item_payloads(factory, parent.id)
    assert payloads[0]["data"] == {"child_run_id": "c1", "summary": "摘要"}
    assert payloads[0]["status"] == items.STATUS_COMPLETED
