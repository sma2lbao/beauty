"""本地 job worker(M0-04):轮询领取、执行、lease/重试。

SQLite 下单进程轮询即可;PG 多实例时把 claim_next 换成 FOR UPDATE SKIP LOCKED。
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from common import metrics
from common.logging import bind_request_context, clear_request_context
from database.session import session_scope
from fastapi import FastAPI

from agent_orchestrator.models.runs import Job
from agent_orchestrator.repositories import JobRepository
from agent_orchestrator.services import runs as runs_service

logger = structlog.get_logger(__name__)
WORKER_ID = f"local-{os.getpid()}"


async def _dispatch(app: FastAPI, session, job: Job) -> None:
    graph = app.state.run_graph
    kind = job.kind
    payload: dict[str, Any] = job.payload or {}
    if kind == "plan":
        await runs_service.plan_run(
            session,
            graph,
            job.tenant_id,
            job.run_id,
            jobs_enabled=False,
            store=app.state.artifact_store,
            workspace=app.state.workspace_client,
            downloader=getattr(app.state, "artifact_downloader", None),
            max_download_files=app.state.settings.artifact_download_max_files,
        )
        return
    if kind == "approve":
        await runs_service.approve_run(
            session,
            graph,
            job.tenant_id,
            job.run_id,
            app.state.artifact_store,
            app.state.workspace_client,
            jobs_enabled=False,
            emit_approved=False,
            downloader=getattr(app.state, "artifact_downloader", None),
            max_download_files=app.state.settings.artifact_download_max_files,
        )
        return
    if kind == "confirm":
        await runs_service.confirm_run(
            session,
            graph,
            job.tenant_id,
            job.run_id,
            str(payload.get("action") or "approve"),
            payload.get("reason"),
            app.state.artifact_store,
            app.state.workspace_client,
            jobs_enabled=False,
            emit_confirmation=False,
            downloader=getattr(app.state, "artifact_downloader", None),
            max_download_files=app.state.settings.artifact_download_max_files,
        )
        return
    if kind == "resume":
        await runs_service.resume_run(
            session,
            graph,
            job.tenant_id,
            job.run_id,
            jobs_enabled=False,
            store=app.state.artifact_store,
            workspace=app.state.workspace_client,
            downloader=getattr(app.state, "artifact_downloader", None),
            max_download_files=app.state.settings.artifact_download_max_files,
        )
        return
    if kind == "retry":
        await runs_service.retry_run(
            session,
            graph,
            job.tenant_id,
            job.run_id,
            jobs_enabled=False,
            store=app.state.artifact_store,
            workspace=app.state.workspace_client,
            downloader=getattr(app.state, "artifact_downloader", None),
            max_download_files=app.state.settings.artifact_download_max_files,
        )
        return
    raise ValueError(f"unknown job kind: {kind}")


async def process_one(app: FastAPI) -> bool:
    """领取并执行一个 job;返回是否实际领到任务。"""
    settings = app.state.settings
    factory = app.state.session_factory
    tenant_id: str | None = None
    job_id: str | None = None
    async with session_scope(factory) as session:
        job = await JobRepository(session).claim_next(
            WORKER_ID, datetime.now(UTC), settings.jobs_lease_seconds
        )
        if job is None:
            return False
        tenant_id = job.tenant_id
        job_id = job.id
    assert tenant_id is not None and job_id is not None

    # 后台任务没有 HTTP 请求:用 job id 作种子,让同一 job 的日志可串联
    bind_request_context(request_id=f"job-{job_id}", tenant_id=tenant_id, job_id=job_id)
    try:
        try:
            async with session_scope(factory) as session:
                job = await JobRepository(session).require_for_tenant(tenant_id, job_id)
                await _dispatch(app, session, job)
        except Exception as exc:  # noqa: BLE001 - worker 必须兜住所有失败
            logger.exception("job failed", job_id=job_id)
            async with session_scope(factory) as session:
                repo = JobRepository(session)
                job = await repo.require_for_tenant(tenant_id, job_id)
                metrics.observe_job_failure(kind=job.kind)
                job.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                job.lease_until = None
                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                else:
                    job.status = "queued"
                    job.run_at = datetime.now(UTC) + timedelta(
                        seconds=min(2**job.attempts, 30)
                    )
            return True

        async with session_scope(factory) as session:
            job = await JobRepository(session).require_for_tenant(tenant_id, job_id)
            job.status = "succeeded"
            job.lease_until = None
            job.locked_by = None
        return True
    finally:
        clear_request_context()


async def worker_loop(app: FastAPI) -> None:
    settings = app.state.settings
    while True:
        try:
            processed = await process_one(app)
        except Exception:  # noqa: BLE001 - 轮询循环不能因单次异常退出
            logger.exception("job worker loop error")
            processed = False
        await asyncio.sleep(0 if processed else settings.jobs_poll_interval_seconds)


def start_worker(app: FastAPI) -> asyncio.Task:
    return asyncio.create_task(worker_loop(app), name="orchestrator-jobs")
