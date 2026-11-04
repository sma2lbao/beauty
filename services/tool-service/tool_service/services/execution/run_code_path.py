"""run_code 执行路径:把模型给的 Python 3 代码经 stdin 交给沙箱 python3(设计 §2)。

代码不进 argv(避免把模型内容拼进命令行、也免去 shell 转义);`python3 -`
把 stdin 当顶层脚本:__name__ == "__main__"、没有 __file__、traceback 文件名是
<stdin>、input() 立即读到 EOF。参数校验与 cwd 夹取与 exec 共用同一套规则。
"""

from __future__ import annotations

from typing import Any

from common.tools import ToolDescriptor

from tool_service.constants import MAX_RUN_CODE_TIMEOUT_SECONDS
from tool_service.services.execution.arguments import resolve_exec_cwd
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)
from tool_service.services.execution.process_path import ProcessExecutionMixin
from tool_service.services.helpers import HELPER_PYTHON


class RunCodeExecutionMixin(ProcessExecutionMixin):
    """run_code 工具:python3 - 从 stdin 读取并执行代码,不走 shell。"""

    async def _run_code(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        started: float,
    ) -> ExecutionOutcome:
        """把 code 经 stdin 交给沙箱 python3,结果与 exec 同构。"""

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
        return await self._run_process(
            context,
            descriptor,
            argv=[HELPER_PYTHON, "-"],
            stdin=str(arguments["code"]),
            cwd=cwd,
            timeout=self._run_code_timeout(context, descriptor, arguments),
            started=started,
            label="代码",
            timeout_hint="缩小代码范围或提高 timeout_seconds;长任务请拆成小步执行",
        )

    def _run_code_timeout(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
    ) -> float:
        """run_code 超时:参数里的 timeout_seconds 覆盖默认,夹在 [1, 120]。"""

        return self._override_timeout(
            context, descriptor, arguments, MAX_RUN_CODE_TIMEOUT_SECONDS
        )
