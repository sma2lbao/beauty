"""job_kill 的执行路径:终止沙箱进程并条件收尾。"""

from __future__ import annotations

from typing import Any

from common.errors import UpstreamError
from common.tools import ToolDescriptor, ToolExecuteResponse

from tool_service.models.background_job import (
    JOB_STATUS_EXITED,
    JOB_STATUS_KILLED,
    JOB_STATUS_LOST,
    JOB_STATUS_RUNNING,
)
from tool_service.services.execution import job_scripts
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)

from .base import _JobBaseMixin
from .logs import _is_gone
from .snapshot import _JobSnapshot


class _JobKillMixin(_JobBaseMixin):
    """job_kill 的执行路径:终止沙箱进程并条件收尾。"""

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
