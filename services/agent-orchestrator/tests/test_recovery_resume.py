"""崩溃续跑(P1-C,见 docs/design/recovery.md §8)。

recover:执行中(planning/running)的 run 进程崩掉后,从该 run 最近的图 checkpoint
续跑——attempts 不变、线程不变;没有 checkpoint 才退化为新 attempt 重新规划。
sweeper 负责把"已无进程推进"的 run 入队 recover。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from agent_orchestrator.main import create_app
from agent_orchestrator.models.runs import Job
from agent_orchestrator.repositories import RunRepository
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services.jobs_worker import process_one
from agent_orchestrator.services.run_recovery_sweeper import sweep_once
from tests.conftest import (
    _IDP,
    ACCOUNT,
    FakeLLM,
    _create_run,
    _settings,
)

ACCOUNT_ID = "t-1"
VERIFY_PASS = '{"summary": "ok", "verdicts": {"日程已整理": true}}'


def _four_replies() -> list[str]:
    return ["# 计划", "执行", VERIFY_PASS, "# 交付物"]


def _events(client, run_id: str) -> list[dict]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT)
    assert resp.status_code == 200
    return resp.json()


def _recovered(client, run_id: str) -> dict:
    return next(e for e in _events(client, run_id) if e["type"] == "run.recovered.v1")


async def _crash_and_recover(settings, app, run_id: str) -> tuple[str, int]:
    """把 run 摆成崩溃现场(状态停在 running)后直接调 recover_run。"""
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            run = await runs_service.require_run(session, ACCOUNT_ID, run_id)
            run.status = "running"
        async with session_scope(factory) as session:
            run = await runs_service.recover_run(
                session,
                app.state.container.run_graph,
                ACCOUNT_ID,
                run_id,
                from_job=True,
                settings=settings,
            )
            return run.status, run.attempts
    finally:
        await factory.engine.dispose()


async def _age_orphan(settings, run_id: str) -> None:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            run = await runs_service.require_run(session, ACCOUNT_ID, run_id)
            run.status = "running"
            run.updated_at = datetime.now(UTC) - timedelta(hours=1)
    finally:
        await factory.engine.dispose()


async def _orphans(settings) -> list[str]:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            rows = await RunRepository(session).list_orphaned_executions(
                datetime.now(UTC), grace_seconds=settings.run_recovery_grace_seconds
            )
            return [run.id for run in rows]
    finally:
        await factory.engine.dispose()


async def _drop_jobs(settings, run_id: str) -> None:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            await session.execute(delete(Job).where(Job.run_id == run_id))
    finally:
        await factory.engine.dispose()


async def _jobs(settings, run_id: str) -> list[Job]:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            rows = await session.scalars(select(Job).where(Job.run_id == run_id))
            return list(rows)
    finally:
        await factory.engine.dispose()


async def _plan_inline(settings, app, run_id: str) -> str:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            run = await runs_service.plan_run(
                session,
                app.state.container.run_graph,
                ACCOUNT_ID,
                run_id,
                settings=settings,
            )
            return run.status
    finally:
        await factory.engine.dispose()


async def _enqueue(settings, kind: str, run_id: str, *, attempts: int = 0) -> None:
    """入队一条 job。attempts>0 用来伪造「已被领取过、执行中崩掉」的现场。"""
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            await jobs_service.enqueue(session, ACCOUNT_ID, kind, run_id)
            if attempts:
                await session.execute(
                    update(Job)
                    .where(Job.run_id == run_id, Job.kind == kind)
                    .values(attempts=attempts)
                )
    finally:
        await factory.engine.dispose()


def test_recover_resumes_at_plan_gate_after_crash(tmp_path) -> None:
    fake = FakeLLM(_four_replies())
    settings = _settings(tmp_path, "recover.db", llm_enabled=True)
    app = create_app(settings, llm_client=fake, token_verifier=_IDP.verifier())
    with TestClient(app) as client:
        run_id = _create_run(client)
        plan = client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        assert plan.status_code == 200
        assert len(fake.calls) == 1

        status, attempts = asyncio.run(_crash_and_recover(settings, app, run_id))
        # 从计划闸前的 checkpoint 拉起:不重新规划,交回人工审批
        assert (status, attempts) == ("awaiting_approval", 1)
        assert len(fake.calls) == 1

        payload = _recovered(client, run_id)["payload"]
        assert payload["from_status"] == "running"
        assert payload["next"] == ["approval_gate"]
        # 快照里带着闸口的 interrupt:不重跑已中断的节点,只把闸口语义补落
        assert payload["resumed"] is False
        assert payload["fallback"] is None
        assert payload["checkpoint_id"]

        # 续跑后的闸口照常放行,只补跑审批之后的节点
        approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        assert approved.json()["status"] == "completed"
        assert len(fake.calls) == 4


def test_recover_without_checkpoint_falls_back_to_new_attempt(tmp_path) -> None:
    fake = FakeLLM(_four_replies())
    settings = _settings(tmp_path, "recover-cold.db", llm_enabled=True)
    app = create_app(settings, llm_client=fake, token_verifier=_IDP.verifier())
    with TestClient(app) as client:
        run_id = _create_run(client)  # 从未执行,没有 checkpoint

        status, attempts = asyncio.run(_crash_and_recover(settings, app, run_id))
        assert (status, attempts) == ("awaiting_approval", 2)
        assert len(fake.calls) == 1

        payload = _recovered(client, run_id)["payload"]
        assert payload["fallback"] == "plan"
        assert payload["checkpoint_id"] is None
        assert payload["resumed"] is False


def test_recover_settles_finished_graph_without_new_model_calls(tmp_path) -> None:
    fake = FakeLLM(_four_replies())
    settings = _settings(tmp_path, "recover-done.db", llm_enabled=True)
    app = create_app(settings, llm_client=fake, token_verifier=_IDP.verifier())
    with TestClient(app) as client:
        run_id = _create_run(client)
        planned = client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        assert planned.status_code == 200
        approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        assert approved.json()["status"] == "completed"
        assert len(fake.calls) == 4

        # 图已跑完、只是结果没落库:直接用快照收尾,不再调模型
        status, attempts = asyncio.run(_crash_and_recover(settings, app, run_id))
        assert (status, attempts) == ("completed", 1)
        assert len(fake.calls) == 4

        payload = _recovered(client, run_id)["payload"]
        assert payload["next"] == []
        assert payload["resumed"] is False
        assert payload["fallback"] is None


def test_recovery_sweeper_enqueues_recover_for_orphan(tmp_path) -> None:
    # jobs_poll_interval 拉长:worker 启动后不会被本用例的 recover job 抢走;
    # sweeper 关掉(interval=0)以便手工调用 sweep_once,避免两个 sweeper 抢跑
    settings = _settings(
        tmp_path,
        "sweeper.db",
        llm_enabled=True,
        jobs_enabled=True,
        jobs_poll_interval_seconds=3600.0,
        run_recovery_sweep_interval_seconds=0.0,
    )
    app = create_app(
        settings, llm_client=FakeLLM(_four_replies()), token_verifier=_IDP.verifier()
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        # 崩溃现场:入队的 plan job 随进程一起丢了(事务回滚),只剩停在 running 的 run
        asyncio.run(_drop_jobs(settings, run_id))
        asyncio.run(_age_orphan(settings, run_id))
        assert asyncio.run(_jobs(settings, run_id)) == []

        container = app.state.container
        assert container.session_factory is not None
        assert asyncio.run(_orphans(settings)) == [run_id]
        assert asyncio.run(sweep_once(container)) == 1
        jobs = asyncio.run(_jobs(settings, run_id))
        assert [(job.kind, job.status) for job in jobs] == [("recover", "queued")]

        # 已有 live job(刚入队的 queued)时不再重复入队
        assert asyncio.run(sweep_once(container)) == 0
        assert len(asyncio.run(_jobs(settings, run_id))) == 1


def test_worker_redirects_stuck_action_job_to_recover(tmp_path) -> None:
    """旧动作 job(approve)撞上崩溃后的 running:改道续跑,不再 409。"""
    settings = _settings(
        tmp_path,
        "worker.db",
        llm_enabled=True,
        jobs_enabled=True,
        jobs_poll_interval_seconds=3600.0,
        run_recovery_sweep_interval_seconds=0.0,
    )
    fake = FakeLLM(_four_replies())
    app = create_app(settings, llm_client=fake, token_verifier=_IDP.verifier())
    with TestClient(app) as client:
        run_id = _create_run(client)
        asyncio.run(_drop_jobs(settings, run_id))
        # 先同步跑出计划闸的 checkpoint(直接调服务,不经 job)
        assert asyncio.run(_plan_inline(settings, app, run_id)) == "awaiting_approval"

        # 崩溃现场:状态停在 running;approve job 已被领取过一次(attempts=1)又崩掉,
        # 租约过期后被重领(attempts=2),此时重放 approve 只会 409
        asyncio.run(_age_orphan(settings, run_id))
        asyncio.run(_enqueue(settings, "approve", run_id, attempts=1))

        assert asyncio.run(process_one(app.state.container)) is True

        run = client.get(f"/api/v1/runs/{run_id}", headers=ACCOUNT).json()
        assert run["status"] == "awaiting_approval"
        assert run["attempts"] == 1
        assert len(fake.calls) == 1  # 计划节点没有被重跑
        jobs = asyncio.run(_jobs(settings, run_id))
        assert [(job.kind, job.status) for job in jobs] == [("approve", "succeeded")]
        payload = _recovered(client, run_id)["payload"]
        assert payload["next"] == ["approval_gate"]
        assert payload["fallback"] is None
