"""exec 执行路径:在沙箱工作区内直跑 shell 命令(设计 §6)。

exec 需要真实退出码与 stderr,不走 helper 的 JSON 信封;输出按字符上限
裁剪、超长时回缩整包结果,并把超时与上游故障翻译成模型可见的错误信封。
"""

from __future__ import annotations

import asyncio
from typing import Any

from common.errors import UpstreamError
from common.tools import ToolDescriptor, ToolExecuteResponse

from tool_service.constants import (
    MAX_EXEC_STDERR_CHARS,
    MAX_EXEC_STDOUT_CHARS,
    MAX_EXEC_TIMEOUT_SECONDS,
    MAX_TOOL_CHARS,
)
from tool_service.services.execution import limits
from tool_service.services.execution.arguments import resolve_exec_cwd
from tool_service.services.execution.exec_output import (
    _clip_exec_stream,
    _fit_exec_result,
    _is_exec_timeout,
)
from tool_service.services.execution.limits import (
    ERROR_DETAIL_CHARS,
    EXEC_NOTES_RESERVE,
    EXEC_SHELL,
)
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)
from tool_service.services.sandbox import SandboxProvider, scope_key_for


class ExecExecutionMixin:
    """exec 工具直跑:真实退出码 + 输出裁剪。"""

    _sandbox: SandboxProvider

    async def _run_exec(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        started: float,
    ) -> ExecutionOutcome:
        """在沙箱工作区内直跑一条 shell 命令(不经 helper,拿真实退出码)。"""

        cwd = resolve_exec_cwd(arguments.get("cwd"))
        if cwd is None:
            return self._failure(
                context,
                descriptor,
                "tool.invalid_args",
                "cwd 必须落在工作区 /workspace 内",
                hint="用相对路径(如 reports)或 /workspace 下的绝对路径",
                duration_ms=_elapsed_ms(started),
            )
        command = str(arguments["command"])
        timeout = self._exec_timeout(context, descriptor, arguments)
        scope_key = scope_key_for(
            context.tenant_id,
            sandbox_scope=context.sandbox_scope,
            conversation_id=context.conversation_id,
            run_id=context.run_id,
        )
        try:
            sandbox_id = await asyncio.wait_for(
                self._sandbox.ensure(
                    context.tenant_id, scope_key=scope_key, run_id=context.run_id
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
        try:
            result = await asyncio.wait_for(
                self._sandbox.exec(
                    context.tenant_id,
                    sandbox_id,
                    [EXEC_SHELL, "-lc", command],
                    cwd=cwd,
                    timeout_seconds=max(1, int(timeout)),
                ),
                timeout + limits.SANDBOX_CALL_GRACE_SECONDS,
            )
        except TimeoutError:
            return self._failure(
                context,
                descriptor,
                "tool.timeout",
                f"命令执行超时({int(timeout)} 秒)",
                hint="缩小命令范围或提高 timeout_seconds;长任务请拆成小步执行",
                retryable=True,
                duration_ms=_elapsed_ms(started),
                sandbox_id=sandbox_id,
            )
        except UpstreamError as exc:
            return self._upstream_failure(
                context, descriptor, exc, started, sandbox_id=sandbox_id
            )
        finally:
            await self._touch(context, sandbox_id)
        duration_ms = result.duration_ms or _elapsed_ms(started)
        stdout, stdout_clipped = _clip_exec_stream(
            result.stdout, MAX_EXEC_STDOUT_CHARS, keep="head"
        )
        stderr, stderr_clipped = _clip_exec_stream(
            result.stderr, MAX_EXEC_STDERR_CHARS, keep="tail"
        )
        payload: dict[str, Any] = {
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "cwd": cwd,
            "duration_ms": duration_ms,
        }
        payload, shrunk = _fit_exec_result(payload, MAX_TOOL_CHARS - EXEC_NOTES_RESERVE)
        notes: list[str] = []
        if stdout_clipped or (shrunk and stdout):
            notes.append("stdout 已截断:超长输出请重定向到文件后用 read 分段读取")
        if stderr_clipped or (shrunk and stderr):
            notes.append("stderr 已截断(保留末尾):完整错误请重定向到文件后用 read 读取")
        if notes:
            payload["notes"] = notes
        response = ToolExecuteResponse(
            ok=True,
            result=payload,
            truncated=bool(result.truncated)
            or stdout_clipped
            or stderr_clipped
            or shrunk,
            duration_ms=duration_ms,
            tool_call_id=context.tool_call_id,
            idempotent_replay=False,
        )
        return ExecutionOutcome(
            descriptor=descriptor, response=response, sandbox_id=sandbox_id
        )

    def _exec_timeout(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
    ) -> float:
        """exec 超时:参数里的 timeout_seconds 覆盖默认,夹在 [1, 120]。"""

        requested = arguments.get("timeout_seconds")
        if isinstance(requested, bool) or not isinstance(requested, int):
            return self._timeout_seconds(context, descriptor)
        return float(min(max(requested, 1), MAX_EXEC_TIMEOUT_SECONDS))

    def _upstream_failure(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        exc: UpstreamError,
        started: float,
        *,
        sandbox_id: str | None = None,
    ) -> ExecutionOutcome:
        """沙箱侧故障:超时归 tool.timeout(命令被终止),其余归 tool.upstream_error。"""

        if _is_exec_timeout(exc):
            return self._failure(
                context,
                descriptor,
                "tool.timeout",
                "沙箱内命令执行超时",
                hint="命令被沙箱超时终止(exit_code 124);请提高 timeout_seconds",
                retryable=True,
                duration_ms=_elapsed_ms(started),
                sandbox_id=sandbox_id,
            )
        return self._failure(
            context,
            descriptor,
            "tool.upstream_error",
            "沙箱不可用或返回异常",
            hint=str(exc)[:ERROR_DETAIL_CHARS],
            retryable=True,
            duration_ms=_elapsed_ms(started),
            sandbox_id=sandbox_id,
        )
