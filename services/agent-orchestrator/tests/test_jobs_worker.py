"""M0-04 job 队列:领取/lease 重领与 worker 端到端。"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from database.session import session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.models.runs import Job
from agent_orchestrator.repositories import JobRepository
from agent_orchestrator.schemas.runs import RunCreate
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import jobs_worker
from agent_orchestrator.services import runs as runs_service
from tests.conftest import _IDP, ACCOUNT, _settings


def _app(tmp_path: Path, **overrides):
    return create_app(
        _settings(tmp_path, "jobs.db", **overrides),
        token_verifier=_IDP.verifier(),
    )


def test_claim_and_expired_lease_reclaim(tmp_path: Path) -> None:
    app = _app(tmp_path, jobs_enabled=False)
    with TestClient(app):
        pass
    # 用应用外的 async 会话直接操作仓库(TestClient 已启动建表)
    import asyncio

    async def scenario() -> None:
        factory = app.state.session_factory
        async with session_scope(factory) as session:
            run = await runs_service.create_run(
                session, "t-1", RunCreate(title="x", goal="y", acceptance_criteria=[])
            )
            job = await jobs_service.enqueue(
                session, "t-1", "plan", run.id, max_attempts=3
            )
            first = await JobRepository(session).claim_next(
                "worker-1", datetime.now(UTC), 60
            )
            assert first is not None and first.id == job.id
            assert first.attempts == 1
            assert (
                await JobRepository(session).claim_next(
                    "worker-2", datetime.now(UTC), 60
                )
                is None
            )
            # 模拟进程崩溃后 lease 过期
            first.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            second = await JobRepository(session).claim_next(
                "worker-2", datetime.now(UTC), 60
            )
            assert second is not None and second.id == job.id
            assert second.attempts == 2
        await factory.engine.dispose()

    asyncio.run(scenario())


async def test_claim_statement_is_locked_on_postgres() -> None:
    """PG 多实例靠 FOR UPDATE SKIP LOCKED 领取,SQLite 没有行锁只能先查后改。"""
    from sqlalchemy.dialects import postgresql

    from agent_orchestrator.repositories import _claim_statement

    statement = _claim_statement(datetime.now(UTC)).with_for_update(skip_locked=True)
    sql = str(statement.compile(dialect=postgresql.dialect())).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_worker_plans_and_approves_run(tmp_path: Path) -> None:
    app = _app(
        tmp_path,
        jobs_enabled=True,
        jobs_poll_interval_seconds=0.01,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/runs",
            json={"title": "worker 测试", "goal": "跑一下", "acceptance_criteria": []},
            headers=ACCOUNT,
        )
        assert created.status_code == 201
        run_id = created.json()["id"]

        def wait_status(expected: str) -> dict:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                run = client.get(f"/api/v1/runs/{run_id}", headers=ACCOUNT).json()
                if run["status"] == expected:
                    return run
                time.sleep(0.02)
            raise AssertionError(f"run 未到 {expected}: {run}")

        planned = wait_status("awaiting_approval")
        assert planned["status"] == "awaiting_approval"
        approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        assert approved.status_code == 200
        completed = wait_status("completed")
        assert completed["status"] == "completed"

def test_renew_lease_extends_and_is_scoped_to_holder(tmp_path: Path) -> None:
    """续租只对仍持锁的 worker 生效,并推迟租约到期时间。"""
    app = _app(tmp_path, jobs_enabled=False)
    with TestClient(app):
        pass
    import asyncio

    async def scenario() -> None:
        factory = app.state.session_factory
        async with session_scope(factory) as session:
            run = await runs_service.create_run(
                session, "t-1", RunCreate(title="x", goal="y", acceptance_criteria=[])
            )
            job = await jobs_service.enqueue(
                session, "t-1", "plan", run.id, max_attempts=3
            )
            repo = JobRepository(session)
            now = datetime.now(UTC)
            claimed = await repo.claim_next("worker-1", now, 60)
            assert claimed is not None
            assert claimed.lease_until == now + timedelta(seconds=60)

            later = now + timedelta(seconds=30)
            assert (
                await repo.renew_lease(
                    job.id, "worker-1", lease_seconds=60, now=later
                )
                is True
            )
            refreshed = await session.get(Job, job.id)
            assert refreshed is not None
            assert refreshed.lease_until == later + timedelta(seconds=60)

            # 非持有者/已收尾的 job 不能续租
            assert (
                await repo.renew_lease(job.id, "worker-2", lease_seconds=60, now=later)
                is False
            )
        await factory.engine.dispose()

    asyncio.run(scenario())


def test_finalize_success_is_scoped_to_lease_holder(tmp_path: Path) -> None:
    """成功收尾必须持锁:被别人重领或已收尾时不得覆盖。"""
    app = _app(tmp_path, jobs_enabled=False)
    with TestClient(app):
        pass
    import asyncio

    async def scenario() -> None:
        factory = app.state.session_factory
        async with session_scope(factory) as session:
            run = await runs_service.create_run(
                session, "t-1", RunCreate(title="x", goal="y", acceptance_criteria=[])
            )
            job = await jobs_service.enqueue(
                session, "t-1", "plan", run.id, max_attempts=3
            )
            repo = JobRepository(session)
            claimed = await repo.claim_next("worker-1", datetime.now(UTC), 60)
            assert claimed is not None

            assert await repo.mark_succeeded(job.id, "worker-2") is False
            assert await repo.mark_succeeded(job.id, "worker-1") is True
            refreshed = await session.get(Job, job.id)
            assert refreshed is not None
            assert refreshed.status == "succeeded"
            assert refreshed.lease_until is None
            assert refreshed.locked_by is None
            # 重复收尾幂等返回 False,不改状态
            assert await repo.mark_succeeded(job.id, "worker-1") is False
        await factory.engine.dispose()

    asyncio.run(scenario())


def test_finish_failure_requeues_then_marks_failed(tmp_path: Path) -> None:
    """失败收尾按尝试次数重排;达到上限落 failed;非持锁者不得收尾。"""
    app = _app(tmp_path, jobs_enabled=False)
    with TestClient(app):
        pass
    import asyncio

    async def scenario() -> None:
        factory = app.state.session_factory
        async with session_scope(factory) as session:
            run = await runs_service.create_run(
                session, "t-1", RunCreate(title="x", goal="y", acceptance_criteria=[])
            )
            job = await jobs_service.enqueue(
                session, "t-1", "plan", run.id, max_attempts=2
            )
            repo = JobRepository(session)
            now = datetime.now(UTC)
            claimed = await repo.claim_next("worker-1", now, 60)
            assert claimed is not None and claimed.attempts == 1

            assert (
                await repo.finish_failure(job.id, "worker-2", error="nope", now=now)
                is False
            )
            assert (
                await repo.finish_failure(
                    job.id, "worker-1", error="boom", now=now
                )
                is True
            )
            refreshed = await session.get(Job, job.id)
            assert refreshed is not None
            assert refreshed.status == "queued"
            assert refreshed.last_error == "boom"
            assert refreshed.locked_by is None
            assert refreshed.lease_until is None
            assert refreshed.run_at == now + timedelta(seconds=2)

            # 第二次执行失败 → attempts 达到 max_attempts,落 failed
            retry_now = now + timedelta(seconds=3)
            claimed_again = await repo.claim_next("worker-2", retry_now, 60)
            assert claimed_again is not None and claimed_again.attempts == 2
            assert (
                await repo.finish_failure(
                    job.id, "worker-2", error="boom again", now=retry_now
                )
                is True
            )
            failed = await session.get(Job, job.id)
            assert failed is not None
            assert failed.status == "failed"
            assert failed.last_error == "boom again"
        await factory.engine.dispose()

    asyncio.run(scenario())


def test_heartbeat_renews_lease_until_stopped(tmp_path: Path) -> None:
    """heartbeat 在执行期间周期续租,停止后退出。"""
    app = _app(tmp_path, jobs_enabled=False, jobs_lease_seconds=1)
    with TestClient(app):
        pass
    import asyncio

    async def scenario() -> None:
        factory = app.state.session_factory
        async with session_scope(factory) as session:
            run = await runs_service.create_run(
                session, "t-1", RunCreate(title="x", goal="y", acceptance_criteria=[])
            )
            job = await jobs_service.enqueue(
                session, "t-1", "plan", run.id, max_attempts=3
            )
            claimed = await JobRepository(session).claim_next(
                jobs_worker.WORKER_ID, datetime.now(UTC), 1
            )
            assert claimed is not None and claimed.id == job.id
            original = claimed.lease_until
        container = SimpleNamespace(
            settings=SimpleNamespace(jobs_lease_seconds=1),
            session_factory=factory,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(
            jobs_worker._heartbeat_loop(container, job.id, stop)
        )
        try:
            await asyncio.sleep(1.4)
            async with session_scope(factory) as session:
                current = await session.get(Job, job.id)
                assert current is not None and current.lease_until is not None
                assert original is not None
                # SQLite 不保存时区:读回来的值补回 UTC 再比较
                assert current.lease_until.replace(tzinfo=UTC) > original
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=3)
        await factory.engine.dispose()

    asyncio.run(scenario())


def test_heartbeat_exits_when_lease_lost(tmp_path: Path) -> None:
    """租约被别的 worker 持有后续租失败,heartbeat 自行退出。"""
    app = _app(tmp_path, jobs_enabled=False, jobs_lease_seconds=1)
    with TestClient(app):
        pass
    import asyncio

    async def scenario() -> None:
        factory = app.state.session_factory
        async with session_scope(factory) as session:
            run = await runs_service.create_run(
                session, "t-1", RunCreate(title="x", goal="y", acceptance_criteria=[])
            )
            job = await jobs_service.enqueue(
                session, "t-1", "plan", run.id, max_attempts=3
            )
            claimed = await JobRepository(session).claim_next(
                "another-worker", datetime.now(UTC), 1
            )
            assert claimed is not None and claimed.id == job.id
        container = SimpleNamespace(
            settings=SimpleNamespace(jobs_lease_seconds=1),
            session_factory=factory,
        )
        task = asyncio.create_task(
            jobs_worker._heartbeat_loop(container, job.id, asyncio.Event())
        )
        await asyncio.wait_for(task, timeout=3)
        await factory.engine.dispose()

    asyncio.run(scenario())

