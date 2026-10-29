"""HTTP 执行器路径:只读工具代理到下游服务(超时/大小上限与沙箱同口径)。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from common.tools import ToolDescriptor

from tool_service.constants import MAX_TOOL_CHARS
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)
from tool_service.services.executors.http import HttpToolExecutor


class HttpExecutionMixin:
    """经 HTTP 执行器跑一次只读代理(设计 §3/§7)。"""

    _http: HttpToolExecutor | None

    async def _run_http(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        started: float,
    ) -> ExecutionOutcome:
        """经 HTTP 执行器跑一次只读代理(超时/大小上限与沙箱执行同口径)。"""

        if self._http is None:
            return self._failure(
                context,
                descriptor,
                "tool.executor_unavailable",
                "该工具的下游执行器尚未接线",
                hint="http 执行器未装配",
                duration_ms=_elapsed_ms(started),
            )
        timeout = self._timeout_seconds(context, descriptor)
        try:
            response = await asyncio.wait_for(
                self._http.execute(context.tenant_id, descriptor.name, arguments),
                timeout,
            )
        except TimeoutError:
            return self._failure(
                context,
                descriptor,
                "tool.timeout",
                f"工具 {descriptor.name} 执行超时",
                hint=f"超时 {int(timeout)} 秒;请稍后重试",
                retryable=True,
                duration_ms=_elapsed_ms(started),
            )
        response = replace(
            response,
            duration_ms=_elapsed_ms(started),
            tool_call_id=context.tool_call_id,
            idempotent_replay=False,
        )
        if response.ok and self._result_chars(response) > MAX_TOOL_CHARS:
            return self._failure(
                context,
                descriptor,
                "tool.too_large",
                f"工具结果超过 {MAX_TOOL_CHARS} 字符上限",
                hint="缩小检索范围(更精确的 query/更小的 limit)后重试",
                retryable=True,
                duration_ms=_elapsed_ms(started),
            )
        return ExecutionOutcome(descriptor=descriptor, response=response)
