"""子代理等待 sweeper(L2 ④,见 docs/design/subagents.md §6.2)。

父 run 停在 awaiting_subagent 等子 run 终态唤醒,两条路会把它永久挂住:
- 漏唤醒:子 run 已终态但唤醒 job 没落地(崩溃/队列丢失);
- 等太久:子 run 卡在长任务或停在需要人工的状态。
本文件覆盖:sweeper 的漏唤醒补偿、已有 live job 时不重复入队、未超时不打扰、
超时级联取消子 run 并唤醒父 run、父 run 不是等待态时跳过,以及计时口径的两处
边界——子 run 停在等人工时不取消(等人≠卡住)、计时基线只算在飞子 run(已终态的
历史子 run 不再把新子 run 一出生就判超时)。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from agent_orchestrator.main import create_app
from agent_orchestrator.models.runs import Job, Run
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services.run_lifecycle import require_run
from agent_orchestrator.services.subagent_wait_sweeper import (
    WAIT_TIMEOUT_REASON,
    sweep_once,
)
from tests.conftest import _settings

ACCOUNT_ID = "t-1"


async def _create(
    settings,
    *,
    status: str,
    parent_run_id: str | None = None,
    parent_kind: str | None = None,
    title: str = "父任务",
) -> str:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            run = Run(
                account_id=ACCOUNT_ID,
                title=title,
                goal="整理竞品定价",
                status=status,
                parent_run_id=parent_run_id,
                parent_kind=parent_kind,
            )
            session.add(run)
            await session.flush()
            return run.id
    finally:
        await factory.engine.dispose()


async def _reload(settings, run_id: str) -> Run:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            return await require_run(session, ACCOUNT_ID, run_id)
    finally:
        await factory.engine.dispose()


async def _jobs(settings, run_id: str) -> list[Job]:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            rows = await session.scalars(
                select(Job).where(Job.run_id == run_id).order_by(Job.created_at, Job.id)
            )
            return list(rows.all())
    finally:
        await factory.engine.dispose()


async def _set_created_at(settings, run_id: str, when: datetime) -> None:
    """把 run 的 created_at 改到过去,模拟「很早以前就完成的子 run」。"""

    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            await session.execute(
                update(Run).where(Run.id == run_id).values(created_at=when)
            )
    finally:
        await factory.engine.dispose()


def _app(tmp_path, name: str, **overrides):
    # worker 轮询拉长(不会被本用例的 job 抢走),等待 sweeper 关掉(手工调用)
    settings = _settings(
        tmp_path,
        name,
        jobs_enabled=True,
        jobs_poll_interval_seconds=3600.0,
        subagent_wait_sweep_interval_seconds=0.0,
        **overrides,
    )
    return settings, create_app(settings)


def test_repairs_lost_wake_up_when_children_finished(tmp_path) -> None:
    settings, app = _app(tmp_path, "sweep-lost.db")
    with TestClient(app):
        parent_id = asyncio.run(
            _create(settings, status=RunStatus.AWAITING_SUBAGENT.value)
        )
        asyncio.run(
            _create(
                settings,
                status=RunStatus.COMPLETED.value,
                parent_run_id=parent_id,
                parent_kind="subagent",
                title="子任务",
            )
        )
        container = app.state.container
        assert asyncio.run(sweep_once(container)) == 1
        jobs = asyncio.run(_jobs(settings, parent_id))
        assert [(job.kind, job.status) for job in jobs] == [("subagent", "queued")]
        # 已有 live job:再扫一轮不重复入队(幂等)
        assert asyncio.run(sweep_once(container)) == 0
        assert len(asyncio.run(_jobs(settings, parent_id))) == 1


def test_leaves_live_child_alone_before_timeout(tmp_path) -> None:
    settings, app = _app(tmp_path, "sweep-live.db")
    with TestClient(app):
        parent_id = asyncio.run(
            _create(settings, status=RunStatus.AWAITING_SUBAGENT.value)
        )
        child_id = asyncio.run(
            _create(
                settings,
                status=RunStatus.RUNNING.value,
                parent_run_id=parent_id,
                parent_kind="subagent",
                title="子任务",
            )
        )
        assert asyncio.run(sweep_once(app.state.container)) == 0
        assert asyncio.run(_reload(settings, child_id)).status == "running"
        assert asyncio.run(_jobs(settings, parent_id)) == []


def test_times_out_cancels_children_and_wakes_parent(tmp_path) -> None:
    settings, app = _app(
        tmp_path, "sweep-timeout.db", subagent_wait_timeout_seconds=0.001
    )
    with TestClient(app):
        parent_id = asyncio.run(
            _create(settings, status=RunStatus.AWAITING_SUBAGENT.value)
        )
        child_id = asyncio.run(
            _create(
                settings,
                status=RunStatus.PLANNING.value,
                parent_run_id=parent_id,
                parent_kind="subagent",
                title="子任务",
            )
        )
        assert asyncio.run(sweep_once(app.state.container)) == 1
        child = asyncio.run(_reload(settings, child_id))
        assert child.status == "cancelled"
        assert child.status_reason == WAIT_TIMEOUT_REASON
        # cancel_run 内部已 notify_parent;若它没入队,sweeper 需要兜底补一条
        jobs = asyncio.run(_jobs(settings, parent_id))
        assert [job.kind for job in jobs] == ["subagent"]


def test_skips_parent_that_is_no_longer_waiting(tmp_path) -> None:
    settings, app = _app(tmp_path, "sweep-skip.db")
    with TestClient(app):
        parent_id = asyncio.run(_create(settings, status=RunStatus.COMPLETED.value))
        asyncio.run(
            _create(
                settings,
                status=RunStatus.COMPLETED.value,
                parent_run_id=parent_id,
                parent_kind="subagent",
                title="子任务",
            )
        )
        assert asyncio.run(sweep_once(app.state.container)) == 0
        assert asyncio.run(_jobs(settings, parent_id)) == []


def test_does_not_cancel_child_waiting_for_human(tmp_path) -> None:
    # 子 run 停在待审批:人还没来得及点,父方计时不能把它取消掉
    settings, app = _app(
        tmp_path, "sweep-human.db", subagent_wait_timeout_seconds=0.001
    )
    with TestClient(app):
        parent_id = asyncio.run(
            _create(settings, status=RunStatus.AWAITING_SUBAGENT.value)
        )
        child_id = asyncio.run(
            _create(
                settings,
                status=RunStatus.AWAITING_APPROVAL.value,
                parent_run_id=parent_id,
                parent_kind="subagent",
                title="子任务",
            )
        )
        assert asyncio.run(sweep_once(app.state.container)) == 0
        assert asyncio.run(_reload(settings, child_id)).status == "awaiting_approval"
        parent = asyncio.run(_reload(settings, parent_id))
        assert parent.status == RunStatus.AWAITING_SUBAGENT.value
        assert asyncio.run(_jobs(settings, parent_id)) == []


def test_ignores_finished_children_when_measuring_wait(tmp_path) -> None:
    # 老子 run 早已完成(2h 前建);新子 run 刚出生,不能被旧基线判超时
    settings, app = _app(
        tmp_path, "sweep-baseline.db", subagent_wait_timeout_seconds=60.0
    )
    with TestClient(app):
        parent_id = asyncio.run(
            _create(settings, status=RunStatus.AWAITING_SUBAGENT.value)
        )
        old_id = asyncio.run(
            _create(
                settings,
                status=RunStatus.COMPLETED.value,
                parent_run_id=parent_id,
                parent_kind="subagent",
                title="旧子任务",
            )
        )
        asyncio.run(
            _set_created_at(settings, old_id, datetime.now(UTC) - timedelta(hours=2))
        )
        child_id = asyncio.run(
            _create(
                settings,
                status=RunStatus.RUNNING.value,
                parent_run_id=parent_id,
                parent_kind="subagent",
                title="新子任务",
            )
        )
        assert asyncio.run(sweep_once(app.state.container)) == 0
        assert asyncio.run(_reload(settings, child_id)).status == "running"
        assert asyncio.run(_jobs(settings, parent_id)) == []


def test_skips_parent_without_children(tmp_path) -> None:
    settings, app = _app(tmp_path, "sweep-orphan.db")
    with TestClient(app):
        asyncio.run(_create(settings, status=RunStatus.AWAITING_SUBAGENT.value))
        # 没有子 run 是数据异常,交给 resume_subagent 落 failed,sweeper 不动
        assert asyncio.run(sweep_once(app.state.container)) == 0
