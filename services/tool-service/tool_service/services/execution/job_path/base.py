"""作业路径的公共辅助:读行、条件收尾、读状态、终止与过期清理。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from common.errors import UpstreamError
from common.tools import ToolDescriptor
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tool_service.core.config import Settings
from tool_service.models.background_job import JOB_STATUS_LOST
from tool_service.repositories import BackgroundJobRepository
from tool_service.services.execution import job_scripts, limits
from tool_service.services.execution.models import ExecutionContext, ExecutionOutcome
from tool_service.services.helpers import WORKSPACE_ROOT

from .constants import _LOST_HINT, _STATUS_SCRIPT_TIMEOUT_SECONDS, log
from .snapshot import _JobReading, _JobSnapshot, _reading, _snapshot


class _JobBaseMixin:
    """作业路径的公共辅助:读行、条件收尾、读状态、终止与过期清理。"""

    async def _load_job(
        self,
        factory: async_sessionmaker[AsyncSession],
        account_id: str,
        job_id: str,
    ) -> _JobSnapshot | None:
        async with session_scope(factory) as session:
            row = await BackgroundJobRepository(session).get_for_account(
                account_id, job_id
            )
            return _snapshot(row) if row is not None else None

    async def _load_job_or_snapshot(
        self,
        factory: async_sessionmaker[AsyncSession],
        account_id: str,
        job_id: str,
        fallback: _JobSnapshot,
    ) -> _JobSnapshot:
        refreshed = await self._load_job(factory, account_id, job_id)
        return refreshed or fallback

    async def _finish_job(
        self,
        factory: async_sessionmaker[AsyncSession],
        account_id: str,
        job_id: str,
        *,
        status: str,
        exit_code: int | None = None,
    ) -> bool:
        """条件 UPDATE 收尾;返回 False 表示已被并发调用方收尾。"""
        async with session_scope(factory) as session:
            return await BackgroundJobRepository(session).mark_finished(
                account_id,
                job_id,
                status=status,
                exit_code=exit_code,
            )

    async def _read_job(
        self,
        context: ExecutionContext,
        sandbox_id: str,
        paths: job_scripts.JobPaths,
        stdout_chars: int,
        stderr_chars: int,
    ) -> _JobReading:
        argv = job_scripts.status_argv(
            paths, stdout_chars=stdout_chars, stderr_chars=stderr_chars
        )
        result = await asyncio.wait_for(
            self._sandbox.exec(
                context.account_id,
                sandbox_id,
                argv,
                cwd=WORKSPACE_ROOT,
                timeout_seconds=int(_STATUS_SCRIPT_TIMEOUT_SECONDS),
            ),
            _STATUS_SCRIPT_TIMEOUT_SECONDS + limits.SANDBOX_CALL_GRACE_SECONDS,
        )
        if result.exit_code != 0:
            log.warning(
                "job.output.status_script_failed",
                account_id=context.account_id,
                exit_code=result.exit_code,
            )
        return _reading(result.stdout)

    async def _try_read_job(
        self,
        context: ExecutionContext,
        sandbox_id: str,
        paths: job_scripts.JobPaths,
        stdout_chars: int,
        stderr_chars: int,
    ) -> _JobReading | None:
        """终态作业补读日志:沙箱不可用/超时都只返回 None,不改库状态。"""
        try:
            return await self._read_job(
                context, sandbox_id, paths, stdout_chars, stderr_chars
            )
        except (TimeoutError, UpstreamError) as exc:
            log.warning(
                "job.output.logs_unavailable",
                account_id=context.account_id,
                job_id=paths.job_id,
                error=str(exc),
            )
            return None

    async def _try_terminate(
        self,
        context: ExecutionContext,
        sandbox_id: str,
        paths: job_scripts.JobPaths,
    ) -> _JobReading | None:
        """超时收尾时的尽力终止;失败只留日志(status 仍记 timeout)。"""
        try:
            return await self._terminate(context, sandbox_id, paths)
        except (TimeoutError, UpstreamError) as exc:
            log.warning(
                "job.output.terminate_failed",
                account_id=context.account_id,
                job_id=paths.job_id,
                error=str(exc),
            )
            return None

    async def _terminate(
        self,
        context: ExecutionContext,
        sandbox_id: str,
        paths: job_scripts.JobPaths,
    ) -> _JobReading:
        argv = job_scripts.kill_argv(paths)
        timeout = 15.0
        result = await asyncio.wait_for(
            self._sandbox.exec(
                context.account_id,
                sandbox_id,
                argv,
                cwd=WORKSPACE_ROOT,
                timeout_seconds=int(timeout),
            ),
            timeout + limits.SANDBOX_CALL_GRACE_SECONDS,
        )
        return _reading(result.stdout)

    async def _best_effort_kill(
        self,
        context: ExecutionContext,
        sandbox_id: str,
        paths: job_scripts.JobPaths,
    ) -> None:
        try:
            await self._terminate(context, sandbox_id, paths)
        except Exception as exc:  # noqa: BLE001 - 收拾残局不允许再抛
            log.warning(
                "job.start.cleanup_failed",
                account_id=context.account_id,
                job_id=paths.job_id,
                error=str(exc),
            )

    async def _mark_lost_and_report(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        snapshot: _JobSnapshot,
        paths: job_scripts.JobPaths,
        factory: async_sessionmaker[AsyncSession],
        started: float,
        *,
        state: str,
    ) -> ExecutionOutcome:
        """沙箱 404:把 running 记成 lost 再按 lost 回信封(ok=true)。"""
        log.warning(
            "job.output.sandbox_gone",
            account_id=context.account_id,
            job_id=paths.job_id,
            state=state,
        )
        await self._finish_job(
            factory, context.account_id, paths.job_id, status=JOB_STATUS_LOST
        )
        return self._job_output_outcome(
            context,
            descriptor,
            snapshot,
            started,
            status=JOB_STATUS_LOST,
            exit_code=None,
            stdout="",
            stderr="",
            note=_LOST_HINT,
            finished_at=datetime.now(UTC),
        )

    async def _cleanup_expired_jobs(
        self,
        context: ExecutionContext,
        sandbox_id: str,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        """清理超保留期的作业目录与登记行;全程 best-effort(只留 warning)。"""
        hours = max(1, int(settings.job_retention_hours or 24))
        argv = job_scripts.cleanup_argv(older_than_minutes=hours * 60)
        try:
            await asyncio.wait_for(
                self._sandbox.exec(
                    context.account_id,
                    sandbox_id,
                    argv,
                    cwd=WORKSPACE_ROOT,
                    timeout_seconds=int(_STATUS_SCRIPT_TIMEOUT_SECONDS),
                ),
                _STATUS_SCRIPT_TIMEOUT_SECONDS + limits.SANDBOX_CALL_GRACE_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响启动
            log.warning(
                "job.cleanup.files_failed",
                account_id=context.account_id,
                error=str(exc),
            )
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        try:
            async with session_scope(factory) as session:
                repo = BackgroundJobRepository(session)
                stale = await repo.list_terminal_before(context.account_id, cutoff)
                if stale:
                    await repo.delete_by_ids(
                        context.account_id, [row.id for row in stale]
                    )
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响启动
            log.warning(
                "job.cleanup.rows_failed",
                account_id=context.account_id,
                error=str(exc),
            )
