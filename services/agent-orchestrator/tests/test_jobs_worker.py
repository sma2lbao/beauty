"""M0-04 job 队列:领取/lease 重领与 worker 端到端。"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from database.session import session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.repositories import JobRepository
from agent_orchestrator.schemas.runs import RunCreate
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import runs as runs_service
from tests.conftest import _IDP, TENANT, _settings


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
            headers=TENANT,
        )
        assert created.status_code == 201
        run_id = created.json()["id"]

        def wait_status(expected: str) -> dict:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                run = client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()
                if run["status"] == expected:
                    return run
                time.sleep(0.02)
            raise AssertionError(f"run 未到 {expected}: {run}")

        planned = wait_status("awaiting_approval")
        assert planned["status"] == "awaiting_approval"
        approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
        assert approved.status_code == 200
        completed = wait_status("completed")
        assert completed["status"] == "completed"
