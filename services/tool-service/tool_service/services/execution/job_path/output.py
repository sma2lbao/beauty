"""job_output 的执行路径:读沙箱状态、lazy 超时收尾、日志回缩回信封。"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from common.errors import UpstreamError
from common.tools import ToolDescriptor, ToolExecuteResponse

from tool_service.models.background_job import (
    JOB_STATUS_EXITED,
    JOB_STATUS_KILLED,
    JOB_STATUS_LOST,
    JOB_STATUS_RUNNING,
    JOB_STATUS_TIMEOUT,
)
from tool_service.services.execution import job_scripts
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)

from .base import _JobBaseMixin
from .constants import _LOST_HINT, _NOEXIT_HINT
from .logs import (
    _fit_job_logs,
    _is_gone,
    _output_notes,
    _stderr_chars,
    _tail_chars,
    _timeout_note,
    _wait_seconds,
)
from .snapshot import _as_utc, _iso_utc, _JobSnapshot


class _JobOutputMixin(_JobBaseMixin):
    """job_output 的执行路径:读沙箱状态、lazy 超时收尾、日志回缩回信封。"""

    def _job_output_outcome(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        snapshot: _JobSnapshot,
        started: float,
        *,
        status: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        note: str = "",
        tail_chars: int | None = None,
        finished_at: datetime | None = None,
    ) -> ExecutionOutcome:
        effective_tail = tail_chars or _tail_chars(self._settings, {})
        payload: dict[str, Any] = {
            "job_id": snapshot.job_id,
            "status": status,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": _elapsed_ms(started),
            "started_at": _iso_utc(snapshot.created_at),
            "finished_at": _iso_utc(finished_at or snapshot.finished_at),
            "notes": _output_notes(effective_tail, status, note),
        }
        payload, truncated = _fit_job_logs(payload)
        return ExecutionOutcome(
            descriptor=descriptor,
            response=ToolExecuteResponse(
                ok=True,
                result=payload,
                truncated=truncated,
                duration_ms=_elapsed_ms(started),
                tool_call_id=context.tool_call_id,
            ),
            sandbox_id=snapshot.sandbox_id,
        )

    async def _run_job_output(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        started: float,
    ) -> ExecutionOutcome:
        settings = self._settings
        job_id = str(arguments.get("job_id") or "")
        if not job_scripts.is_valid_job_id(job_id):
            return self._failure(
                context,
                descriptor,
                "tool.invalid_args",
                "job_id 必须是 32 位小写十六进制",
                duration_ms=_elapsed_ms(started),
            )
        factory = self._session_factory
        if factory is None:
            return self._failure(
                context,
                descriptor,
                "tool.upstream_error",
                "后台作业未配置数据库会话,无法读取作业",
                duration_ms=_elapsed_ms(started),
            )
        tail_chars = _tail_chars(settings, arguments)
        stderr_chars = _stderr_chars(tail_chars)
        wait_seconds = _wait_seconds(arguments)
        deadline = time.monotonic() + wait_seconds

        while True:
            snapshot = await self._load_job(factory, context.account_id, job_id)
            if snapshot is None:
                return self._failure(
                    context,
                    descriptor,
                    "tool.not_found",
                    f"后台作业 {job_id} 不存在或不属于当前账号",
                    hint="job_id 来自 job_start 的返回;过期的作业会被清理",
                    duration_ms=_elapsed_ms(started),
                )
            paths = job_scripts.job_paths(job_id)
            if snapshot.status == JOB_STATUS_LOST:
                return self._job_output_outcome(
                    context,
                    descriptor,
                    snapshot,
                    started,
                    tail_chars=tail_chars,
                    status=JOB_STATUS_LOST,
                    exit_code=None,
                    stdout="",
                    stderr="",
                    note=_LOST_HINT,
                )

            if snapshot.status != JOB_STATUS_RUNNING:
                # 终态:状态以库为准,日志尽量再从沙箱读一次(读不到不算错)。
                reading = await self._try_read_job(
                    context, snapshot.sandbox_id, paths, tail_chars, stderr_chars
                )
                return self._job_output_outcome(
                    context,
                    descriptor,
                    snapshot,
                    started,
                    tail_chars=tail_chars,
                    status=snapshot.status,
                    exit_code=snapshot.exit_code,
                    stdout=reading.stdout if reading else "",
                    stderr=reading.stderr if reading else "",
                    note="" if reading else _LOST_HINT,
                )

            runtime_seconds = max(1, int(settings.job_max_runtime_seconds or 600))
            age = (datetime.now(UTC) - _as_utc(snapshot.created_at)).total_seconds()
            if age > runtime_seconds:
                # lazy timeout:发现超龄就终止进程组并落 timeout(条件 UPDATE)。
                reading = await self._try_terminate(context, snapshot.sandbox_id, paths)
                status = JOB_STATUS_TIMEOUT
                exit_code: int | None = None
                if reading is not None and reading.state == job_scripts.STATE_EXITED:
                    status = JOB_STATUS_EXITED
                    exit_code = reading.exit_code
                await self._finish_job(
                    factory,
                    context.account_id,
                    job_id,
                    status=status,
                    exit_code=exit_code,
                )
                return self._job_output_outcome(
                    context,
                    descriptor,
                    snapshot,
                    started,
                    tail_chars=tail_chars,
                    status=status,
                    exit_code=exit_code,
                    stdout=reading.stdout if reading else "",
                    stderr=reading.stderr if reading else "",
                    note=_timeout_note(runtime_seconds, reading),
                    finished_at=datetime.now(UTC),
                )

            try:
                reading = await self._read_job(
                    context, snapshot.sandbox_id, paths, tail_chars, stderr_chars
                )
            except TimeoutError:
                return self._failure(
                    context,
                    descriptor,
                    "tool.timeout",
                    "读取后台作业状态超时",
                    hint="稍后重试;持续超时请检查 workspace-service",
                    retryable=True,
                    duration_ms=_elapsed_ms(started),
                    sandbox_id=snapshot.sandbox_id,
                )
            except UpstreamError as exc:
                if _is_gone(exc):
                    return await self._mark_lost_and_report(
                        context,
                        descriptor,
                        snapshot,
                        paths,
                        factory,
                        started,
                        state="lost",
                    )
                return self._upstream_failure(
                    context, descriptor, exc, started, sandbox_id=snapshot.sandbox_id
                )

            if reading.state == job_scripts.STATE_EXITED:
                await self._finish_job(
                    factory,
                    context.account_id,
                    job_id,
                    status=JOB_STATUS_EXITED,
                    exit_code=reading.exit_code,
                )
                return self._job_output_outcome(
                    context,
                    descriptor,
                    snapshot,
                    started,
                    tail_chars=tail_chars,
                    status=JOB_STATUS_EXITED,
                    exit_code=reading.exit_code,
                    stdout=reading.stdout,
                    stderr=reading.stderr,
                    finished_at=datetime.now(UTC),
                )
            if reading.state == job_scripts.STATE_KILLED:
                await self._finish_job(
                    factory,
                    context.account_id,
                    job_id,
                    status=JOB_STATUS_KILLED,
                    exit_code=None,
                )
                return self._job_output_outcome(
                    context,
                    descriptor,
                    snapshot,
                    started,
                    tail_chars=tail_chars,
                    status=JOB_STATUS_KILLED,
                    exit_code=None,
                    stdout=reading.stdout,
                    stderr=reading.stderr,
                    finished_at=datetime.now(UTC),
                )
            if reading.state in (
                job_scripts.STATE_NOEXIT,
                job_scripts.STATE_MISSING,
                job_scripts.STATE_UNKNOWN,
            ):
                await self._finish_job(
                    factory,
                    context.account_id,
                    job_id,
                    status=JOB_STATUS_LOST,
                    exit_code=None,
                )
                return self._job_output_outcome(
                    context,
                    descriptor,
                    snapshot,
                    started,
                    tail_chars=tail_chars,
                    status=JOB_STATUS_LOST,
                    exit_code=None,
                    stdout=reading.stdout,
                    stderr=reading.stderr,
                    note=_NOEXIT_HINT,
                    finished_at=datetime.now(UTC),
                )

            # 还在 running:wait_seconds 内再等一轮,否则返回当前快照。
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._job_output_outcome(
                    context,
                    descriptor,
                    snapshot,
                    started,
                    tail_chars=tail_chars,
                    status=JOB_STATUS_RUNNING,
                    exit_code=None,
                    stdout=reading.stdout,
                    stderr=reading.stderr,
                )
            await asyncio.sleep(min(1.0, remaining))
