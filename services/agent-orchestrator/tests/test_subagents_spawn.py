"""子代理委派 P1-E 单测:工具参数、上限、继承、完整 spawn、取消/超时与投影。

约定:临时 sqlite 文件库 + 真实 Run 行;子 run 的执行用桩图(只实现
``ainvoke``)——本文件验证的是 spawn 的编排语义(建行/事件/轮询收尾/用量),
真实图行为由既有 run 测试覆盖,零网络。
本文件承载原 ``test_subagents.py`` 拆分后的完整 spawn 生命周期与取消/超时部分。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.graph import items
from agent_orchestrator.models.runs import PARENT_KIND_SUBAGENT, Run, RunEvent
from agent_orchestrator.services import subagents
from tests.conftest import _settings

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
