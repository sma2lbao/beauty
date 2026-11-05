"""连接器执行路径:把连接器工具转发给 connector-service(C2.4)。

超时/结果大小上限与 HTTP 执行器同口径;工具级失败由 connector-service 以
HTTP 200 + ok=false 信封返回,这一层原样透传,只补齐 duration_ms/tool_call_id。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from common.tools import ToolDescriptor

from tool_service.constants import MAX_TOOL_CHARS
from tool_service.services.connectors import ConnectorGateway
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)


class ConnectorExecutionMixin:
    """经 ConnectorGateway 执行一次连接器工具调用(设计 §5.3/§5.7)。"""

    _connector: ConnectorGateway | None

    async def _run_connector(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        started: float,
    ) -> ExecutionOutcome:
        """一次性转发;网络/超时映射 tool.connector_unavailable/timeout。"""

        if self._connector is None:
            return self._failure(
                context,
                descriptor,
                "tool.executor_unavailable",
                "该工具的下游执行器尚未接线",
                hint="connector 执行器未装配",
                duration_ms=_elapsed_ms(started),
            )
        timeout = self._timeout_seconds(context, descriptor)
        try:
            execution = await asyncio.wait_for(
                self._connector.execute(
                    context.account_id,
                    descriptor,
                    arguments,
                    run_id=context.run_id,
                ),
                timeout,
            )
        except TimeoutError:
            return self._failure(
                context,
                descriptor,
                "tool.connector_timeout",
                f"工具 {descriptor.name} 执行超时",
                hint=f"超时 {int(timeout)} 秒;请稍后重试",
                retryable=True,
                duration_ms=_elapsed_ms(started),
            )
        response = replace(
            execution.response,
            duration_ms=_elapsed_ms(started),
            tool_call_id=context.tool_call_id,
            idempotent_replay=False,
        )
        if response.ok and self._result_chars(response) > MAX_TOOL_CHARS:
            # 结果过大仍保留连接 id,审计才能回溯到具体连接。
            return replace(
                self._failure(
                    context,
                    descriptor,
                    "tool.too_large",
                    f"工具结果超过 {MAX_TOOL_CHARS} 字符上限",
                    hint="缩小返回范围后重试",
                    retryable=True,
                    duration_ms=_elapsed_ms(started),
                ),
                connector_connection_id=execution.connection_id,
            )
        return ExecutionOutcome(
            descriptor=descriptor,
            response=response,
            connector_connection_id=execution.connection_id,
        )
