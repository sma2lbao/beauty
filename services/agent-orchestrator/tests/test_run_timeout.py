"""run 执行超时与调度优先级(P1-C,见 docs/design/recovery.md §4)。

覆盖:生效超时(run 级覆盖全局)、基准 coalesce(started_at, created_at)、
条件收尾后的事件/固定文案/job 取消、迟到结果丢弃(is_superseded),以及
job 按 priority DESC 领取。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_orchestrator.main import create_app
from agent_orchestrator.models.runs import Job, Run, RunEvent
from agent_orchestrator.repositories import JobRepository
from agent_orchestrator.schemas.runs import RunCreate, RunStatus
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import run_timeout_sweeper
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services.run_events import is_superseded
from agent_orchestrator.services.run_execution_approvals import _apply_graph_result
from tests.conftest import _IDP, _settings

TIMEOUT_REASON = "执行超时（1 秒），可重试"


def _boot(tmp_path: Path):
    """建表(走 lifespan)并返回测试 settings;应用本身 jobs 关闭,不跑后台 worker。"""
    settings = _settings(tmp_path, "jobs.db")
    with TestClient(create_app(settings, token_verifier=_IDP.verifier())):
        pass
    return settings


def _sweep_container(settings, factory, **overrides) -> SimpleNamespace:
    """sweeper 只依赖 settings 与 session_factory,不需要完整容器。"""
    sweep_settings = settings.model_copy(
        update={"jobs_enabled": True, "run_timeout_seconds": 1, **overrides}
    )
    return SimpleNamespace(settings=sweep_settings, session_factory=factory)


async def _new_run(session, **kwargs) -> Run:
    return await runs_service.create_run(
        session,
        "t-1",
        RunCreate(title="超时", goal="跑一下", acceptance_criteria=[], **kwargs),
    )


def test_overdue_run_fails_and_reports(tmp_path: Path) -> None:
    """超过生效超时 → failed + 固定文案 + run.timeout.v1 + 排队 job 取消。"""
    settings = _boot(tmp_path)

    async def scenario() -> dict[str, object]:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                run = await _new_run(session)
                run.status = RunStatus.RUNNING.value
                run.started_at = datetime.now(UTC) - timedelta(seconds=5)
                await jobs_service.enqueue(session, "t-1", "plan", run.id)
                run_id = run.id
            container = _sweep_container(settings, factory)
            first = await run_timeout_sweeper.sweep_once(container)
            # 已终态:再次扫描不会重复收尾(条件 UPDATE 的守卫)
            second = await run_timeout_sweeper.sweep_once(container)
            async with session_scope(factory) as session:
                stored = await session.get(Run, run_id)
                job = await session.scalar(select(Job).where(Job.run_id == run_id))
                event = await session.scalar(
                    select(RunEvent).where(
                        RunEvent.run_id == run_id,
                        RunEvent.type == run_timeout_sweeper.TIMEOUT_EVENT,
                    )
                )
                assert stored is not None and event is not None and job is not None
                values = {
                    "first": first,
                    "second": second,
                    "status": stored.status,
                    "reason": stored.status_reason,
                    "job_status": job.status,
                    "payload": dict(event.payload),
                }
            return values
        finally:
            await factory.engine.dispose()

    result = asyncio.run(scenario())
    assert result["first"] == 1
    assert result["second"] == 0
    assert result["status"] == RunStatus.FAILED.value
    assert result["reason"] == TIMEOUT_REASON
    assert result["job_status"] == "cancelled"
    payload = result["payload"]
    assert payload == {
        "timeout_seconds": 1,
        "started_at": payload["started_at"],
        "elapsed_seconds": payload["elapsed_seconds"],
        "from_status": RunStatus.RUNNING.value,
    }
    assert payload["elapsed_seconds"] >= 5
    assert datetime.fromisoformat(str(payload["started_at"])).tzinfo is not None


def test_global_timeout_disabled_skips_sweep(tmp_path: Path) -> None:
    """全局超时 <= 0(默认关)时整轮不扫:配置关闭就必须真的不动作。"""
    settings = _boot(tmp_path)

    async def scenario() -> tuple[int, str]:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                run = await _new_run(session)
                run.status = RunStatus.RUNNING.value
                run.started_at = datetime.now(UTC) - timedelta(seconds=30)
                run_id = run.id
            container = _sweep_container(settings, factory, run_timeout_seconds=0)
            handled = await run_timeout_sweeper.sweep_once(container)
            async with session_scope(factory) as session:
                stored = await session.get(Run, run_id)
                assert stored is not None
                return handled, stored.status
        finally:
            await factory.engine.dispose()

    handled, status = asyncio.run(scenario())
    assert handled == 0
    assert status == RunStatus.RUNNING.value


def test_run_level_timeout_overrides_global(tmp_path: Path) -> None:
    """run 显式超时优先:60 秒未到不判;显式 0 表示该 run 不超时。"""
    settings = _boot(tmp_path)

    async def scenario() -> tuple[int, list[str]]:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                slow = await _new_run(session, timeout_seconds=60)
                slow.status = RunStatus.RUNNING.value
                slow.started_at = datetime.now(UTC) - timedelta(seconds=5)
                never = await _new_run(session, timeout_seconds=0)
                never.status = RunStatus.RUNNING.value
                never.started_at = datetime.now(UTC) - timedelta(seconds=5)
                ids = [slow.id, never.id]
            container = _sweep_container(settings, factory)
            handled = await run_timeout_sweeper.sweep_once(container)
            async with session_scope(factory) as session:
                statuses = []
                for run_id in ids:
                    stored = await session.get(Run, run_id)
                    assert stored is not None
                    statuses.append(stored.status)
            return handled, statuses
        finally:
            await factory.engine.dispose()

    handled, statuses = asyncio.run(scenario())
    assert handled == 0
    assert statuses == [RunStatus.RUNNING.value, RunStatus.RUNNING.value]


def test_timeout_basis_falls_back_to_created_at(tmp_path: Path) -> None:
    """started_at 为空(从未进 planning/running)时以 created_at 为基准。"""
    settings = _boot(tmp_path)

    async def scenario() -> dict[str, object]:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                run = await _new_run(session)
                run.created_at = datetime.now(UTC) - timedelta(seconds=30)
                run_id = run.id
            container = _sweep_container(settings, factory)
            handled = await run_timeout_sweeper.sweep_once(container)
            async with session_scope(factory) as session:
                stored = await session.get(Run, run_id)
                event = await session.scalar(
                    select(RunEvent).where(
                        RunEvent.run_id == run_id,
                        RunEvent.type == run_timeout_sweeper.TIMEOUT_EVENT,
                    )
                )
                assert stored is not None and event is not None
                return {
                    "handled": handled,
                    "status": stored.status,
                    "from_status": dict(event.payload)["from_status"],
                }
        finally:
            await factory.engine.dispose()

    result = asyncio.run(scenario())
    assert result["handled"] == 1
    assert result["status"] == RunStatus.FAILED.value
    assert result["from_status"] == RunStatus.PENDING.value


def test_superseded_statuses_are_detected_and_late_results_dropped(
    tmp_path: Path,
) -> None:
    """cancelled/failed 都算被取代;迟到图结果不得覆盖终态。"""
    settings = _boot(tmp_path)

    async def scenario() -> tuple[dict[str, str | None], list[str], bool]:
        factory = create_session_factory(settings.database_url)
        try:
            ids: dict[str, str] = {}
            async with session_scope(factory) as session:
                for status in (
                    RunStatus.COMPLETED.value,
                    RunStatus.FAILED.value,
                    RunStatus.CANCELLED.value,
                    RunStatus.RUNNING.value,
                ):
                    run = await _new_run(session)
                    run.status = status
                    await session.flush()
                    ids[status] = run.id
                superseded = {
                    status: await is_superseded(session, run_id)
                    for status, run_id in ids.items()
                }
                failed_run = await session.get(Run, ids[RunStatus.FAILED.value])
                assert failed_run is not None
                await _apply_graph_result(session, failed_run, {}, None, None)
                late_statuses = [failed_run.status]
                completed_run = await session.get(Run, ids[RunStatus.COMPLETED.value])
                assert completed_run is not None
                # 终态行即使被直接调用也不会被超时逻辑再判一次
                retimed = await run_timeout_sweeper._timeout_one(
                    session,
                    "t-1",
                    completed_run.id,
                    _sweep_container(settings, factory).settings,
                )
                events = await session.scalar(
                    select(RunEvent).where(
                        RunEvent.run_id == completed_run.id,
                        RunEvent.type == run_timeout_sweeper.TIMEOUT_EVENT,
                    )
                )
                return superseded, late_statuses, retimed and events is not None
        finally:
            await factory.engine.dispose()

    superseded, late_statuses, wrongly_timed_out = asyncio.run(scenario())
    assert superseded == {
        RunStatus.COMPLETED.value: None,
        RunStatus.FAILED.value: RunStatus.FAILED.value,
        RunStatus.CANCELLED.value: RunStatus.CANCELLED.value,
        RunStatus.RUNNING.value: None,
    }
    assert late_statuses == [RunStatus.FAILED.value]
    assert wrongly_timed_out is False


def test_jobs_are_claimed_by_priority_then_run_at(tmp_path: Path) -> None:
    """priority DESC, run_at, created_at:高优先级先被领取,入队复制 run.priority。"""
    settings = _boot(tmp_path)

    async def scenario() -> dict[str, object]:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                low = await _new_run(session, priority=1)
                high = await _new_run(session, priority=5)
                override = await _new_run(session, priority=0)
                low_job = await jobs_service.enqueue(session, "t-1", "plan", low.id)
                high_job = await jobs_service.enqueue(session, "t-1", "plan", high.id)
                override_job = await jobs_service.enqueue(
                    session, "t-1", "plan", override.id, priority=9
                )
                first = await JobRepository(session).claim_next(
                    "worker-1", datetime.now(UTC), 60
                )
                second = await JobRepository(session).claim_next(
                    "worker-1", datetime.now(UTC), 60
                )
                third = await JobRepository(session).claim_next(
                    "worker-1", datetime.now(UTC), 60
                )
                assert first and second and third
                return {
                    "copied": (low_job.priority, high_job.priority),
                    "override": override_job.priority,
                    "order": [first.run_id, second.run_id, third.run_id],
                    "expected": [override.id, high.id, low.id],
                }
        finally:
            await factory.engine.dispose()

    result = asyncio.run(scenario())
    assert result["copied"] == (1, 5)
    assert result["override"] == 9
    assert result["order"] == result["expected"]
