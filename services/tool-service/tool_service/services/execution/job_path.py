"""job_start / job_output / job_kill 的执行路径(设计 §6)。

作业进程活在沙箱里,登记行活在 tool-service 自己的库(background_jobs):

- 起步顺序是"先起进程,再落库",落库失败就尽力把刚起的进程干掉再报错;
- 状态收尾一律走条件 UPDATE(running → 终态),并发的 job_output / job_kill
  只有一方能改,另一方按"已被收尾"读回,不覆盖;
- 沙箱丢失(404 / 不可用)记 lost,而不是把 running 永远挂在那儿;
- 超时是 lazy 的:只在某次 job_output / job_kill 时才发现并收尾,没有后台
  sweeper(设计 §10 的残余风险),长期无人查看的作业会占着并发配额。

作业目录与脚本拼装都在 job_scripts 里(纯函数),这里只管状态机与信封。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from common.errors import UpstreamError
from common.logging import get_logger
from common.tools import ToolDescriptor, ToolExecuteResponse
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tool_service.constants import MAX_TOOL_CHARS
from tool_service.core.config import Settings
from tool_service.models.background_job import (
    JOB_STATUS_EXITED,
    JOB_STATUS_KILLED,
    JOB_STATUS_LOST,
    JOB_STATUS_RUNNING,
    JOB_STATUS_TIMEOUT,
    BackgroundJob,
)
from tool_service.repositories import BackgroundJobRepository
from tool_service.services.execution import job_scripts, limits
from tool_service.services.execution.arguments import resolve_exec_cwd
from tool_service.services.execution.exec_output import _payload_chars
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)
from tool_service.services.helpers import WORKSPACE_ROOT
from tool_service.services.sandbox import SandboxProvider, scope_key_for

log = get_logger("tool.jobs")

#: 读状态/终止脚本本身的超时(脚本只做 tail 与 kill,远小于工具超时)。
_STATUS_SCRIPT_TIMEOUT_SECONDS = 10.0
#: 整包回缩的预留:信封里除日志还有 job_id/时间/notes 等字段。
_LOG_FIT_RESERVE = 800
#: 单路日志回缩的下限,再小就没有诊断价值了。
_MIN_LOG_CHARS = 200
#: 作业进程"消失但没写退出码"时给模型看的提示。
_NOEXIT_HINT = "进程已消失且未写退出码,可能是沙箱 OOM 或外部终止"
_LOST_HINT = "作业所在沙箱已不可用(可能被回收),作业状态记为 lost"


@dataclass(frozen=True, slots=True)
class _JobSnapshot:
    """登记行在事务内的只读快照(ORM 属性统一在 session 内读出来)。"""

    job_id: str
    sandbox_id: str
    status: str
    exit_code: int | None
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class _JobReading:
    """一次状态脚本的解析结果。"""

    state: str
    exit_code: int | None
    stdout: str
    stderr: str


class JobExecutionMixin:
    """job_* 的执行实现;由 ToolRunner 多继承装配。"""

    _settings: Settings
    _sandbox: SandboxProvider
    _session_factory: async_sessionmaker[AsyncSession] | None

    # ---- job_start ------------------------------------------------------

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

    # ---- job_output -----------------------------------------------------

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

    # ---- job_kill -------------------------------------------------------

    async def _run_job_kill(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        started: float,
    ) -> ExecutionOutcome:
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
                "后台作业未配置数据库会话,无法终止作业",
                duration_ms=_elapsed_ms(started),
            )
        snapshot = await self._load_job(factory, context.account_id, job_id)
        if snapshot is None:
            return self._failure(
                context,
                descriptor,
                "tool.not_found",
                f"后台作业 {job_id} 不存在或不属于当前账号",
                duration_ms=_elapsed_ms(started),
            )
        if snapshot.status != JOB_STATUS_RUNNING:
            # 已结束的作业不报错(幂等):回显当前状态与真实退出码。
            return self._job_kill_outcome(
                context,
                descriptor,
                snapshot,
                started,
                status=snapshot.status,
                exit_code=snapshot.exit_code,
                already_finished=True,
            )

        paths = job_scripts.job_paths(job_id)
        try:
            reading = await self._terminate(context, snapshot.sandbox_id, paths)
        except TimeoutError:
            return self._failure(
                context,
                descriptor,
                "tool.timeout",
                "终止后台作业超时",
                hint="进程可能仍在运行;稍后可用 job_output 复查",
                retryable=True,
                duration_ms=_elapsed_ms(started),
                sandbox_id=snapshot.sandbox_id,
            )
        except UpstreamError as exc:
            if _is_gone(exc):
                await self._finish_job(
                    factory,
                    context.account_id,
                    job_id,
                    status=JOB_STATUS_LOST,
                )
                return self._job_kill_outcome(
                    context,
                    descriptor,
                    snapshot,
                    started,
                    status=JOB_STATUS_LOST,
                    exit_code=None,
                    already_finished=False,
                )
            return self._upstream_failure(
                context, descriptor, exc, started, sandbox_id=snapshot.sandbox_id
            )

        if reading.state == job_scripts.STATE_EXITED:
            status = JOB_STATUS_EXITED
            exit_code = reading.exit_code
        elif reading.state == job_scripts.STATE_KILLED:
            status = JOB_STATUS_KILLED
            exit_code = None
        else:
            # pid 缺失或脚本异常:进程已经不在,按 lost 记(不谎报 killed)。
            status = JOB_STATUS_LOST
            exit_code = None
        await self._finish_job(
            factory,
            context.account_id,
            job_id,
            status=status,
            exit_code=exit_code,
        )
        # 无论条件 UPDATE 是否命中都回读一次:库里的 status 才是真值
        # (snapshot 是 kill 之前的旧快照,直接用会回显 running)。
        final = await self._load_job_or_snapshot(
            factory, context.account_id, job_id, snapshot
        )
        return self._job_kill_outcome(
            context,
            descriptor,
            final,
            started,
            status=final.status,
            exit_code=final.exit_code,
            already_finished=False,
        )

    # ---- 公共辅助 -------------------------------------------------------

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

    def _job_kill_outcome(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        snapshot: _JobSnapshot,
        started: float,
        *,
        status: str,
        exit_code: int | None,
        already_finished: bool,
    ) -> ExecutionOutcome:
        payload: dict[str, Any] = {
            "job_id": snapshot.job_id,
            "status": status,
            "exit_code": exit_code,
            "already_finished": already_finished,
            "notes": (
                ["作业已结束,无需终止;exit_code 为真实退出码"]
                if already_finished
                else []
            ),
        }
        return ExecutionOutcome(
            descriptor=descriptor,
            response=ToolExecuteResponse(
                ok=True,
                result=payload,
                duration_ms=_elapsed_ms(started),
                tool_call_id=context.tool_call_id,
            ),
            sandbox_id=snapshot.sandbox_id,
        )


# ---- 纯函数辅助(便于单测,不碰 IO)------------------------------------


def _snapshot(row: BackgroundJob) -> _JobSnapshot:
    return _JobSnapshot(
        job_id=row.id,
        sandbox_id=row.sandbox_id,
        status=row.status,
        exit_code=row.exit_code,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def _reading(output: str) -> _JobReading:
    state, exit_code = job_scripts.parse_status(output)
    stdout_text, stderr_text = job_scripts.split_logs(output)
    return _JobReading(
        state=state,
        exit_code=exit_code,
        stdout=stdout_text,
        stderr=stderr_text,
    )


def _has_pid(output: str | None) -> bool:
    """启动脚本会回显 PID:<pid>;有它才算进程真的起来了。"""
    text = output or ""
    for line in text.splitlines():
        if line.startswith(job_scripts.PID_PREFIX):
            pid = line[len(job_scripts.PID_PREFIX) :].strip()
            return pid.isdigit()
    return False


def _optional_name(arguments: dict[str, Any]) -> str | None:
    raw = arguments.get("name")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def _tail_chars(settings: Settings, arguments: dict[str, Any]) -> int:
    maximum = max(1000, int(settings.job_output_max_chars or 20000))
    default = min(max(1000, int(settings.job_output_default_chars or 8000)), maximum)
    raw = arguments.get("tail_chars")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return default
    return min(max(raw, 1000), maximum)


def _stderr_chars(tail_chars: int) -> int:
    """stderr 取 stdout 的一半且不超过 4000,保证整个信封仍装得下。"""
    return max(500, min(4000, tail_chars // 2))


def _wait_seconds(arguments: dict[str, Any]) -> int:
    raw = arguments.get("wait_seconds")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return min(max(raw, 0), 15)


def _is_gone(exc: UpstreamError) -> bool:
    """沙箱服务明确回 404(沙箱被回收)才判 lost。"""
    details = getattr(exc, "details", None) or {}
    return details.get("status") == 404


def _as_utc(moment: datetime) -> datetime:
    """SQLite 读回来的 datetime 可能是 naive,统一按 UTC 解释。"""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _iso_utc(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return _as_utc(moment).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timeout_note(runtime_seconds: int, reading: _JobReading | None) -> str:
    if reading is None:
        return f"作业运行超过 {runtime_seconds} 秒,已标记 timeout;终止请求未能送达沙箱"
    return f"作业运行超过 {runtime_seconds} 秒,已终止并标记 timeout"


def _output_notes(tail_chars: int, status: str, note: str) -> list[str]:
    notes = [
        f"stdout 只保留最后 {tail_chars} 字符,stderr 只保留最后"
        f" {_stderr_chars(tail_chars)} 字符;完整输出请 read 日志文件"
    ]
    if note:
        notes.append(note)
    if status == JOB_STATUS_RUNNING:
        notes.append("作业仍在运行,可稍后再用 job_output 查看")
    return notes


def _fit_job_logs(
    payload: dict[str, Any], limit: int = MAX_TOOL_CHARS - _LOG_FIT_RESERVE
) -> tuple[dict[str, Any], bool]:
    """整包回缩到 limit 字符内:保留日志尾部(最新输出),至少各留 200 字符。"""
    adjusted = dict(payload)
    shrunk = False
    while _payload_chars(adjusted) > limit:
        field = max(
            ("stdout", "stderr"),
            key=lambda key: len(str(adjusted.get(key) or "")),
        )
        text = str(adjusted.get(field) or "")
        if len(text) <= _MIN_LOG_CHARS:
            break
        overflow = _payload_chars(adjusted) - limit
        cut = min(len(text) - _MIN_LOG_CHARS, max(overflow + 64, 256))
        if cut <= 0:
            break
        adjusted[field] = text[cut:]
        shrunk = True
    return adjusted, shrunk
