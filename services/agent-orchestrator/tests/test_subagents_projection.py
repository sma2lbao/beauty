"""子代理委派 P1-E 单测:工具参数、上限、继承、完整 spawn、取消/超时与投影。

约定:临时 sqlite 文件库 + 真实 Run 行;子 run 的执行用桩图(只实现
``ainvoke``)——本文件验证的是 spawn 的编排语义(建行/事件/轮询收尾/用量),
真实图行为由既有 run 测试覆盖,零网络。
本文件承载原 ``test_subagents.py`` 拆分后的子代理列表与用量投影部分。
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
from agent_orchestrator.services import subagents, usage_events
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.items import append_item_event, resolve_thread_id
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_quota import _usage_boundary
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
