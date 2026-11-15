"""job_start 的执行路径:先起沙箱进程,再落库。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import uuid4

from common.errors import UpstreamError
from common.tools import ToolDescriptor, ToolExecuteResponse
from database.session import session_scope

from tool_service.models.background_job import JOB_STATUS_RUNNING, BackgroundJob
from tool_service.repositories import BackgroundJobRepository
from tool_service.services.execution import job_scripts, limits
from tool_service.services.execution.arguments import resolve_exec_cwd
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)
from tool_service.services.helpers import WORKSPACE_ROOT
from tool_service.services.sandbox import scope_key_for

from .base import _JobBaseMixin
from .constants import log
from .snapshot import _as_utc, _has_pid, _iso_utc, _optional_name


class _JobStartMixin(_JobBaseMixin):
    """job_start 的执行路径:先起沙箱进程,再落库。"""

    async def _run_job_start(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        started: float,
    ) -> ExecutionOutcome:
        settings = self._settings
        factory = self._session_factory
        command = str(arguments.get("command") or "").strip()
        if not command:
            return self._failure(
                context,
                descriptor,
                "tool.invalid_args",
                "command 不能为空",
                duration_ms=_elapsed_ms(started),
            )
        cwd = resolve_exec_cwd(arguments.get("cwd"))
        if cwd is None:
            return self._failure(
                context,
                descriptor,
                "tool.invalid_args",
                "cwd 必须落在工作区 /workspace 内",
                hint="省略 cwd 时默认 /workspace",
                duration_ms=_elapsed_ms(started),
            )
        if factory is None:
            return self._failure(
                context,
                descriptor,
                "tool.upstream_error",
                "后台作业未配置数据库会话,无法登记作业",
                duration_ms=_elapsed_ms(started),
            )
        timeout = self._timeout_seconds(context, descriptor)
        scope_key = scope_key_for(
            context.account_id,
            sandbox_scope=context.sandbox_scope,
            conversation_id=context.conversation_id,
            run_id=context.run_id,
        )
        try:
            sandbox_id = await asyncio.wait_for(
                self._sandbox.ensure(
                    context.account_id, scope_key=scope_key, run_id=context.run_id
                ),
                timeout + limits.SANDBOX_CALL_GRACE_SECONDS,
            )
        except TimeoutError:
            return self._failure(
                context,
                descriptor,
                "tool.timeout",
                "获取沙箱超时",
                hint="稍后重试;持续超时请检查 workspace-service",
                retryable=True,
                duration_ms=_elapsed_ms(started),
            )
        except UpstreamError as exc:
            return self._upstream_failure(context, descriptor, exc, started)

        limit = max(1, int(settings.job_max_concurrent_per_account or 1))
        try:
            async with session_scope(factory) as session:
                running = await BackgroundJobRepository(session).count_running(
                    context.account_id
                )
        except Exception as exc:  # noqa: BLE001 - 库不可用时按上游故障处理
            log.warning(
                "job.start.count_failed", account_id=context.account_id, error=str(exc)
            )
            return self._failure(
                context,
                descriptor,
                "tool.upstream_error",
                "后台作业配额检查失败",
                retryable=True,
                duration_ms=_elapsed_ms(started),
                sandbox_id=sandbox_id,
            )
        if running >= limit:
            return self._failure(
                context,
                descriptor,
                "tool.budget_exceeded",
                f"后台作业数量已达上限({limit} 个),请先结束不需要的作业",
                hint="用 job_kill 结束不需要的作业,或等已运行的作业结束",
                duration_ms=_elapsed_ms(started),
                sandbox_id=sandbox_id,
            )

        # 清理超保留期的作业(目录与登记行):best-effort,失败只留日志。
        await self._cleanup_expired_jobs(context, sandbox_id, factory, settings)

        job_id = uuid4().hex
        paths = job_scripts.job_paths(job_id)
        argv = job_scripts.start_argv(command=command, cwd=cwd, paths=paths)
        try:
            result = await asyncio.wait_for(
                self._sandbox.exec(
                    context.account_id,
                    sandbox_id,
                    argv,
                    cwd=WORKSPACE_ROOT,
                    timeout_seconds=max(1, int(timeout)),
                ),
                timeout + limits.SANDBOX_CALL_GRACE_SECONDS,
            )
        except TimeoutError:
            return self._failure(
                context,
                descriptor,
                "tool.timeout",
                f"启动后台作业超时({int(timeout)} 秒)",
                hint="cwd 目录过大或沙箱繁忙;稍后重试",
                retryable=True,
                duration_ms=_elapsed_ms(started),
                sandbox_id=sandbox_id,
            )
        except UpstreamError as exc:
            return self._upstream_failure(
                context, descriptor, exc, started, sandbox_id=sandbox_id
            )

        if result.exit_code != 0 or not _has_pid(result.stdout):
            # 启动脚本自身失败(mkdir/cd 失败或没来得及写 pid):收拾残局再报。
            log.warning(
                "job.start.script_failed",
                account_id=context.account_id,
                job_id=job_id,
                exit_code=result.exit_code,
                error=(result.stderr or "")[: limits.ERROR_DETAIL_CHARS],
            )
            await self._best_effort_kill(context, sandbox_id, paths)
            return self._failure(
                context,
                descriptor,
                "tool.upstream_error",
                "后台作业启动失败",
                hint=(result.stderr or result.stdout or "").strip()[
                    : limits.ERROR_DETAIL_CHARS
                ]
                or "检查 command 与 cwd 是否可执行",
                retryable=True,
                duration_ms=_elapsed_ms(started),
                sandbox_id=sandbox_id,
            )

        name = _optional_name(arguments)
        record = BackgroundJob(
            id=job_id,
            account_id=context.account_id,
            sandbox_id=sandbox_id,
            scope_key=scope_key,
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            tool_call_id=context.tool_call_id,
            name=name,
            command=command,
            cwd=cwd,
            status=JOB_STATUS_RUNNING,
        )
        started_at: datetime | None = None
        try:
            async with session_scope(factory) as session:
                record = await BackgroundJobRepository(session).create(record)
                started_at = _as_utc(record.created_at)
        except Exception as exc:  # noqa: BLE001 - 落库失败必须尽量收掉进程
            log.warning(
                "job.start.persist_failed",
                account_id=context.account_id,
                job_id=job_id,
                error=str(exc),
            )
            await self._best_effort_kill(context, sandbox_id, paths)
            return self._failure(
                context,
                descriptor,
                "tool.upstream_error",
                "后台作业登记失败,已尝试终止刚启动的进程",
                retryable=True,
                duration_ms=_elapsed_ms(started),
                sandbox_id=sandbox_id,
            )

        runtime_seconds = max(1, int(settings.job_max_runtime_seconds or 600))
        payload: dict[str, Any] = {
            "job_id": job_id,
            "status": JOB_STATUS_RUNNING,
            "name": name,
            "command": command,
            "cwd": cwd,
            "started_at": _iso_utc(started_at),
            "log_path": paths.directory + "/",
            "notes": [
                "长任务请定期 job_output 查看;"
                f"超过 {runtime_seconds} 秒没有结束会被标记 timeout 并终止",
            ],
        }
        return ExecutionOutcome(
            descriptor=descriptor,
            response=ToolExecuteResponse(
                ok=True,
                result=payload,
                duration_ms=_elapsed_ms(started),
                tool_call_id=context.tool_call_id,
            ),
            sandbox_id=sandbox_id,
        )
