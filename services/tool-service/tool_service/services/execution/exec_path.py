"""exec 执行路径:在沙箱工作区内直跑 shell 命令(设计 §6)。

参数校验与 shell 组装留在本模块;ensure / exec / touch、超时翻译与输出裁剪
由 process_path 的共享实现承载(run_code 同款口径)。
"""

from __future__ import annotations

from typing import Any

from common.tools import ToolDescriptor

from tool_service.constants import MAX_EXEC_TIMEOUT_SECONDS
from tool_service.services.execution.arguments import resolve_exec_cwd
from tool_service.services.execution.limits import EXEC_SHELL
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)
from tool_service.services.execution.process_path import ProcessExecutionMixin


class ExecExecutionMixin(ProcessExecutionMixin):
    """exec 工具:命令交给 /bin/sh -lc 直跑,拿真实退出码。"""

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
        return await self._run_process(
            context,
            descriptor,
            argv=[EXEC_SHELL, "-lc", command],
            stdin=None,
            cwd=cwd,
            timeout=self._exec_timeout(context, descriptor, arguments),
            started=started,
            label="命令",
            timeout_hint="缩小命令范围或提高 timeout_seconds;长任务请拆成小步执行",
        )

    def _exec_timeout(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
    ) -> float:
        """exec 超时:参数里的 timeout_seconds 覆盖默认,夹在 [1, 120]。"""

        return self._override_timeout(
            context, descriptor, arguments, MAX_EXEC_TIMEOUT_SECONDS
        )
