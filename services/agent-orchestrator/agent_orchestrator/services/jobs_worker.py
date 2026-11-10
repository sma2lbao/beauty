"""本地 job worker(M0-04):轮询领取、执行、lease/重试。

SQLite 下单进程轮询即可;PG 多实例由 claim_next 的 FOR UPDATE SKIP LOCKED
保证不重复领取。worker 只依赖 AppContainer(执行图/交付物存储/工作区客户端),
不再读 app.state——装配细节由容器负责。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from common.logging import bind_request_context, clear_request_context
from database.session import session_scope

from agent_orchestrator import metrics
from agent_orchestrator.models.runs import Job
from agent_orchestrator.repositories import JobRepository
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services.post_commit import (
    PostCommitRuntime,
    bind_runtime,
    drain_after_commit,
)

if TYPE_CHECKING:
    from agent_orchestrator.core.container import AppContainer

logger = structlog.get_logger(__name__)
WORKER_ID = f"local-{os.getpid()}"


async def _dispatch(container: AppContainer, session, job: Job) -> None:
    graph = container.run_graph
    kind = job.kind
    payload: dict[str, Any] = job.payload or {}
    common = {
        "jobs_enabled": False,
        "downloader": container.artifact_downloader,
        "max_download_files": container.settings.artifact_download_max_files,
    }
    if kind in {"plan", "resume", "retry"}:
        action = {
            "plan": runs_service.plan_run,
            "resume": runs_service.resume_run,
            "retry": runs_service.retry_run,
        }[kind]
        await action(
            session,
            graph,
            job.account_id,
            job.run_id,
            store=container.artifact_store,
            workspace=container.workspace_client,
            # 后台 job 也要带上配置:记忆召回在 _invoke_plan 里按开关执行
            settings=container.settings,
            # 上下文策略同 API 路径:起图时快照,与入队侧口径一致
            context=container.context_pipeline,
            **common,
        )
        return
    if kind == "approve":
        await runs_service.approve_run(
            session,
            graph,
            job.account_id,
            job.run_id,
            container.artifact_store,
            container.workspace_client,
            emit_approved=False,
            from_job=True,
            **common,
        )
        return
    if kind == "confirm":
        await runs_service.confirm_run(
            session,
            graph,
            job.account_id,
            job.run_id,
            str(payload.get("action") or "approve"),
            payload.get("reason"),
            container.artifact_store,
            container.workspace_client,
            emit_confirmation=False,
            from_job=True,
            **common,
        )
        return
    if kind == "answer":
        # 回答/放弃挂起提问(§10.5):API 侧已落 answered 事件并推进到 running,
        # 这里只恢复图;超时 sweeper 入队时 status 仍是 awaiting_input,由
        # answer_run(from_job=True) 自行推进。
        await runs_service.answer_run(
            session,
            graph,
            job.account_id,
            job.run_id,
            question_id=str(payload.get("question_id") or ""),
            action=str(payload.get("action") or "decline"),
            answers=payload.get("answers") or [],
            reason=payload.get("reason"),
            answered_by=payload.get("answered_by"),
            store=container.artifact_store,
            workspace=container.workspace_client,
            emit_answered=False,
            from_job=True,
            **common,
        )
        return
    if kind == "reject":
        await runs_service.reject_run(
            session,
            graph,
            job.account_id,
            job.run_id,
            str(payload.get("reason") or "已拒绝"),
        )
        return
    raise ValueError(f"unknown job kind: {kind}")


async def _heartbeat_loop(
    container: AppContainer, job_id: str, stop: asyncio.Event
) -> None:
    """执行期间周期续租,避免长 run 被别的 worker 当成租约过期重领。

    间隔取 lease 的 1/3(夹在 1s~30s);续租失败(租约已丢)只告警并退出,
    不打断当前执行——最终收尾由 mark_succeeded/finish_failure 的持锁校验兜底。
    """
    settings = container.settings
    factory = container.session_factory
    if factory is None:  # pragma: no cover - process_one 已 assert
        return
    interval = max(1.0, min(settings.jobs_lease_seconds / 3.0, 30.0))
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        try:
            async with session_scope(factory) as session:
                renewed = await JobRepository(session).renew_lease(
                    job_id, WORKER_ID, lease_seconds=settings.jobs_lease_seconds
                )
        except Exception:  # noqa: BLE001 - 续租失败不能中断执行本身
            logger.exception("job lease renewal failed", job_id=job_id)
            continue
        if not renewed:
            logger.warning("job lease lost; stop renewing", job_id=job_id)
            return


async def process_one(container: AppContainer) -> bool:
    """领取并执行一个 job;返回是否实际领到任务。"""
    settings = container.settings
    factory = container.session_factory
    assert factory is not None
    account_id: str | None = None
    job_id: str | None = None
    job_kind: str | None = None
    async with session_scope(factory) as session:
        job = await JobRepository(session).claim_next(
            WORKER_ID, datetime.now(UTC), settings.jobs_lease_seconds
        )
        if job is None:
            return False
        account_id = job.account_id
        job_id = job.id
        job_kind = job.kind
    assert account_id is not None and job_id is not None

    # 后台任务没有 HTTP 请求:用 job id 作种子,让同一 job 的日志可串联
    bind_request_context(
        request_id=f"job-{job_id}", account_id=account_id, job_id=job_id
    )
    runtime = PostCommitRuntime(
        session_factory=factory,
        llm=container.llm_client,
        settings=container.settings,
    )
    # 执行期间周期续租:长 run 不能被别的 worker 当成"租约过期"重领
    stop_heartbeat = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat_loop(container, job_id, stop_heartbeat),
        name=f"job-heartbeat-{job_id}",
    )
    try:
        try:
            async with session_scope(factory) as session:
                bind_runtime(session, runtime)
                job = await JobRepository(session).require_for_account(
                    account_id, job_id
                )
                await _dispatch(container, session, job)
            # 事务已提交:执行 run 完成等提交后副作用(记忆抽取),失败不影响 job
            await drain_after_commit(session)
        except Exception as exc:  # noqa: BLE001 - worker 必须兜住所有失败
            logger.exception("job failed", job_id=job_id)
            async with session_scope(factory) as session:
                finished = await JobRepository(session).finish_failure(
                    job_id,
                    WORKER_ID,
                    error=f"{type(exc).__name__}: {exc}",
                )
            if finished:
                metrics.observe_job_failure(kind=job_kind or "unknown")
            else:
                # 租约已丢:另一个 worker 已接手,收尾交给它,不要覆盖
                logger.warning("job finalize skipped; lease lost", job_id=job_id)
            return True

        async with session_scope(factory) as session:
            finished = await JobRepository(session).mark_succeeded(job_id, WORKER_ID)
        if not finished:
            # 租约已丢或 job 已被收尾:不要覆盖别人的结果
            logger.warning("job finalize skipped; lease lost", job_id=job_id)
        return True
    finally:
        stop_heartbeat.set()
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        clear_request_context()


async def worker_loop(container: AppContainer) -> None:
    settings = container.settings
    while True:
        try:
            processed = await process_one(container)
        except Exception:  # noqa: BLE001 - 轮询循环不能因单次异常退出
            logger.exception("job worker loop error")
            processed = False
        await asyncio.sleep(0 if processed else settings.jobs_poll_interval_seconds)


def start_worker(container: AppContainer) -> asyncio.Task:
    return asyncio.create_task(worker_loop(container), name="orchestrator-jobs")
