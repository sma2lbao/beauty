"""异步子代理 L2 单测:入队回执、等待节点中断、唤醒与恢复(零网络)。

覆盖 docs/design/subagents.md 的 L2 最小闭环:
- spawn 在 jobs_enabled 时只入队并回 pending 回执,不内联等待;
- subagent_wait 节点 interrupt 挂起,恢复时把子代理回执写成 user 消息;
- 子 run 终态唤醒 awaiting_subagent 的父 run(未等待时 no-op);
- resume_subagent 在子代理全部终态后恢复父图,恢复载荷与过程项收尾正确。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.graph import items
from agent_orchestrator.graph.nodes import make_subagent_wait_node
from agent_orchestrator.graph.nodes.subagent_wait import (
    INTERRUPT_KIND,
    pending_spawn_ack,
)
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.models.runs import Job, Run, RunEvent
from agent_orchestrator.repositories import (
    SUBAGENT_RESUMED_EVENT,
    SUBAGENT_WAITING_EVENT,
)
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import run_subagent_resume, subagent_wake, subagents
from tests.conftest import _settings

ACCOUNT = "t-1"

PARAMS = {
    "title": "调研竞品",
    "task": "整理三家的定价页差异",
    "agent_name": "调研员",
    "instructions": "只做调研,不改代码",
}


class _StubGraph:
    """桩图:走不到 ainvoke(异步路径只入队)。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def ainvoke(self, state: Any, config: Any = None) -> dict[str, Any]:
        self.calls.append(state)
        return {"rounds": 1, "final_status": "completed", "results": ["ok"]}


@pytest.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    settings = _settings(tmp_path, "subagent_async.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


async def _create_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: str = RunStatus.PENDING.value,
    parent_run_id: str | None = None,
    parent_kind: str | None = None,
    title: str = "主任务",
) -> Run:
    async with session_scope(factory) as session:
        run = Run(
            account_id=ACCOUNT,
            title=title,
            goal="把竞品资料整理成表格",
            status=status,
            parent_run_id=parent_run_id,
            parent_kind=parent_kind,
        )
        session.add(run)
        await session.flush()
        return run


async def _reload(factory: async_sessionmaker[AsyncSession], run_id: str) -> Run:
    async with session_scope(factory) as session:
        return await session.get(Run, run_id)


async def _events(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[RunEvent]:
    async with session_scope(factory) as session:
        rows = await session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        )
        return list(rows.all())


async def _jobs(factory: async_sessionmaker[AsyncSession]) -> list[Job]:
    async with session_scope(factory) as session:
        rows = await session.scalars(select(Job).order_by(Job.created_at, Job.id))
        return list(rows.all())


# ---- 契约与节点 ----


def test_pending_spawn_ack_only_accepts_enqueue_receipt() -> None:
    ack = pending_spawn_ack(
        '{"ok": true, "pending": true, "subagent_run_id": "c-1", "title": "调研"}'
    )
    assert ack == {"child_run_id": "c-1", "title": "调研"}
    # 内联路径的普通回执、缺 id 的畸形载荷都不算待等待
    assert pending_spawn_ack('{"ok": true, "subagent_run_id": "c-1"}') is None
    assert pending_spawn_ack('{"pending": true, "subagent_run_id": ""}') is None
    assert pending_spawn_ack("调研完成") is None


async def test_wait_node_interrupts_and_returns_notice_as_user_message() -> None:
    builder = StateGraph(RunGraphState)
    builder.add_node("subagent_wait", make_subagent_wait_node())
    builder.add_edge(START, "subagent_wait")
    builder.add_edge("subagent_wait", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread-wait"}}

    first = await graph.ainvoke(
        {
            "pending_subagents": [{"child_run_id": "c-1", "title": "调研竞品"}],
            "tool_history": [],
        },
        config,
    )
    interrupt_payload = first["__interrupt__"][0].value
    assert interrupt_payload["kind"] == INTERRUPT_KIND
    assert interrupt_payload["children"] == [
        {"child_run_id": "c-1", "title": "调研竞品"}
    ]

    final = await graph.ainvoke(
        Command(
            resume={
                "kind": INTERRUPT_KIND,
                "children": [
                    {
                        "child_run_id": "c-1",
                        "title": "调研竞品",
                        "status": "completed",
                        "summary": "三家定价差异已整理",
                    }
                ],
            }
        ),
        config,
    )
    assert final["pending_subagents"] == []
    history = final["tool_history"]
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert "调研竞品" in history[0]["content"]
    assert "三家定价差异已整理" in history[0]["content"]


# ---- spawn 异步回执 ----


async def test_spawn_with_jobs_enqueues_child_instead_of_waiting(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    settings = _settings(tmp_path, "subagent_async.db", jobs_enabled=True)
    parent = await _create_run(factory, status=RunStatus.PLANNING.value)
    async with session_scope(factory) as session:
        payload = await subagents.spawn_subagent(
            session,
            parent=parent,
            params=dict(PARAMS),
            settings=settings,
            factory=factory,
            run_graph=_StubGraph(),
        )
    assert payload["ok"] is True
    assert payload["pending"] is True
    child_id = payload["subagent_run_id"]
    child = await _reload(factory, child_id)
    assert child.parent_kind == "subagent"
    # 入队的是子 run 的 plan job,父 run 不在这里阻塞
    jobs = await _jobs(factory)
    assert [(job.kind, job.run_id) for job in jobs] == [("plan", child_id)]
    # 父 run 的过程项停在 started(收尾由唤醒时补),没有终态项
    events = await _events(factory, parent.id)
    phases = [
        event.payload["phase"] for event in events if event.type == items.EVENT_TYPE
    ]
    assert phases == [items.PHASE_STARTED]


# ---- 唤醒 ----


async def test_notify_parent_enqueues_subagent_job_only_when_waiting(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _create_run(factory, status=RunStatus.AWAITING_SUBAGENT.value)
    child = await _create_run(
        factory,
        status=RunStatus.COMPLETED.value,
        parent_run_id=parent.id,
        parent_kind="subagent",
    )
    async with session_scope(factory) as session:
        child_row = await session.get(Run, child.id)
        woke = await subagent_wake.notify_parent(session, child_row)
    assert woke is True
    jobs = await _jobs(factory)
    assert [(job.kind, job.run_id) for job in jobs] == [("subagent", parent.id)]

    # 父 run 已经不在等待:静默 no-op,不重复入队
    async with session_scope(factory) as session:
        parent_row = await session.get(Run, parent.id)
        parent_row.status = RunStatus.RUNNING.value
        await session.flush()
        woke = await subagent_wake.notify_parent(
            session, await session.get(Run, child.id)
        )
    assert woke is False
    assert len(await _jobs(factory)) == 1


async def test_notify_parent_ignores_non_subagent_children(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    run = await _create_run(factory, status=RunStatus.COMPLETED.value)
    async with session_scope(factory) as session:
        row = await session.get(Run, run.id)
        assert await subagent_wake.notify_parent(session, row) is False
    assert await _jobs(factory) == []


# ---- 恢复 ----


async def test_resume_subagent_waits_for_all_children(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    settings = _settings(tmp_path, "subagent_async.db", jobs_enabled=True)
    parent = await _create_run(factory, status=RunStatus.AWAITING_SUBAGENT.value)
    await _create_run(
        factory,
        status=RunStatus.COMPLETED.value,
        parent_run_id=parent.id,
        parent_kind="subagent",
    )
    await _create_run(
        factory,
        status=RunStatus.RUNNING.value,
        parent_run_id=parent.id,
        parent_kind="subagent",
    )
    async with session_scope(factory) as session:
        run = await run_subagent_resume.resume_subagent(
            session, _StubGraph(), ACCOUNT, parent.id, settings=settings
        )
    # 还有子代理在飞:只做检查,父 run 仍在等待,也没有 resumed 事件
    assert run.status == RunStatus.AWAITING_SUBAGENT.value
    events = await _events(factory, parent.id)
    assert not [event for event in events if event.type == SUBAGENT_RESUMED_EVENT]


async def test_resume_subagent_resumes_graph_with_notices(
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, "subagent_async.db")
    parent = await _create_run(factory, status=RunStatus.AWAITING_SUBAGENT.value)
    done = await _create_run(
        factory,
        status=RunStatus.COMPLETED.value,
        parent_run_id=parent.id,
        parent_kind="subagent",
        title="调研竞品",
    )
    okay = await _create_run(
        factory,
        status=RunStatus.FAILED.value,
        parent_run_id=parent.id,
        parent_kind="subagent",
        title="起草摘要",
    )
    async with session_scope(factory) as session:
        doc = await session.get(Run, okay.id)
        doc.status_reason = "模型返回空正文"
        from agent_orchestrator.services.run_events import append_event

        await append_event(
            session,
            await session.get(Run, done.id),
            "run.completed.v1",
            {"rounds": 1, "results": ["三家定价差异已整理"]},
        )
        await session.flush()
        # 父 run 里那条 started 的子代理项(异步路径由本模块补终态)
        from agent_orchestrator.services.items import append_item_event

        for child in (done, okay):
            await append_item_event(
                session,
                parent,
                thread_id=parent.id,
                item_id=f"item-{child.id}",
                kind=items.KIND_SUBAGENT,
                phase=items.PHASE_STARTED,
                title=child.title,
                data={"child_run_id": child.id},
                node="execute",
                status=items.STATUS_COMPLETED,
            )
        await session.commit()

    sent: list[Any] = []
    captured: dict[str, Any] = {}

    class _Graph(_StubGraph):
        async def ainvoke(self, state: Any, config: Any = None) -> dict[str, Any]:
            sent.append(state)
            return {"rounds": 1, "final_status": "completed", "results": ["ok"]}

    @asynccontextmanager
    async def _null_stream(*args: Any, **kwargs: Any) -> AsyncIterator[None]:
        yield None

    async def _fake_apply(
        session: Any, run: Any, values: Any, *args: Any, **kwargs: Any
    ):
        captured["applied"] = values
        return run

    monkeypatch.setattr(run_subagent_resume, "_apply_graph_result", _fake_apply)
    for name in ("_delta_stream", "_step_stream", "_item_stream", "_usage_boundary"):
        monkeypatch.setattr(run_subagent_resume, name, lambda *a, **k: _null_stream())

    async with session_scope(factory) as session:
        run = await run_subagent_resume.resume_subagent(
            session, _Graph(), ACCOUNT, parent.id, settings=settings
        )
    assert run.status == RunStatus.RUNNING.value
    assert len(sent) == 1
    command = sent[0]
    assert isinstance(command, Command)
    notices = command.resume["children"]
    assert [notice["child_run_id"] for notice in notices] == [done.id, okay.id]
    assert notices[0]["summary"]
    assert notices[1]["reason"] == "模型返回空正文"
    events = await _events(factory, parent.id)
    assert [event for event in events if event.type == SUBAGENT_RESUMED_EVENT]
    settled = [
        event
        for event in events
        if event.type == items.EVENT_TYPE
        and event.payload["phase"] in (items.PHASE_COMPLETED, items.PHASE_FAILED)
    ]
    assert len(settled) == 2
    assert captured["applied"]["results"] == ["ok"]


async def test_waiting_event_payload_keeps_children(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _create_run(factory, status=RunStatus.AWAITING_SUBAGENT.value)
    async with session_scope(factory) as session:
        from agent_orchestrator.services.run_events import append_event

        await append_event(
            session,
            parent,
            SUBAGENT_WAITING_EVENT,
            {"children": [{"child_run_id": "c-1", "title": "调研"}]},
        )
    events = await _events(factory, parent.id)
    payload = [
        event.payload for event in events if event.type == SUBAGENT_WAITING_EVENT
    ][0]
    assert payload["children"][0]["child_run_id"] == "c-1"
