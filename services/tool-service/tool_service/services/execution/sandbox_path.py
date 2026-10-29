"""沙箱执行路径:helper 协议执行 + 结果信封解析 + 补丁剥离。

宽限常量经 limits 模块属性读取,测试可用 monkeypatch 调小它。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

from common.errors import UpstreamError
from common.tools import ToolDescriptor, ToolExecuteResponse

from tool_service.constants import MAX_TOOL_CHARS
from tool_service.services.execution import limits
from tool_service.services.execution.limits import ERROR_DETAIL_CHARS
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)
from tool_service.services.execution.outcome import _split_patch
from tool_service.services.helpers import build_request, helper_argv
from tool_service.services.sandbox import SandboxProvider, scope_key_for


class SandboxExecutionMixin:
    """经沙箱 helper 执行只读/写工具。"""

    _sandbox: SandboxProvider

    async def _run_sandbox(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        started: float,
        internal_arguments: dict[str, Any] | None = None,
    ) -> ExecutionOutcome:
        helper_arguments = (
            {**arguments, **internal_arguments} if internal_arguments else arguments
        )
        timeout = self._timeout_seconds(context, descriptor)
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
        try:
            result = await asyncio.wait_for(
                self._sandbox.exec(
                    context.tenant_id,
                    sandbox_id,
                    helper_argv(),
                    stdin=build_request(descriptor.name, helper_arguments),
                    timeout_seconds=max(1, int(timeout)),
                ),
                timeout + limits.SANDBOX_CALL_GRACE_SECONDS,
            )
        except TimeoutError:
            return self._failure(
                context,
                descriptor,
                "tool.timeout",
                f"工具 {descriptor.name} 执行超时",
                hint=f"超时 {int(timeout)} 秒;请缩小范围后重试",
                retryable=True,
                duration_ms=_elapsed_ms(started),
                sandbox_id=sandbox_id,
            )
        finally:
            await self._touch(context, sandbox_id)
        duration_ms = result.duration_ms or _elapsed_ms(started)
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout or "").strip()[:ERROR_DETAIL_CHARS]
            return self._failure(
                context,
                descriptor,
                "tool.io_error",
                "沙箱内 helper 执行失败",
                hint=detail,
                retryable=True,
                duration_ms=duration_ms,
                sandbox_id=sandbox_id,
            )
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            return self._failure(
                context,
                descriptor,
                "tool.io_error",
                "helper 输出不是合法 JSON",
                hint=result.stdout.strip()[:ERROR_DETAIL_CHARS],
                retryable=True,
                duration_ms=duration_ms,
                sandbox_id=sandbox_id,
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            return self._failure(
                context,
                descriptor,
                "tool.io_error",
                "helper 输出不符合工具信封契约",
                hint=str(payload)[:ERROR_DETAIL_CHARS],
                retryable=True,
                duration_ms=duration_ms,
                sandbox_id=sandbox_id,
            )
        try:
            parsed = ToolExecuteResponse.from_payload(payload)
        except ValueError:
            return self._failure(
                context,
                descriptor,
                "tool.io_error",
                "工具结果信封无法解析",
                hint=str(payload)[:ERROR_DETAIL_CHARS],
                retryable=True,
                duration_ms=duration_ms,
                sandbox_id=sandbox_id,
            )
        response = replace(
            parsed,
            duration_ms=duration_ms,
            tool_call_id=context.tool_call_id,
            idempotent_replay=False,
        )
        # 补丁只用于审计:先剥离,再算结果体积/回包,避免进模型上下文。
        patch, response = _split_patch(response)
        oversized = self._result_chars(response) > MAX_TOOL_CHARS
        if oversized and response.ok:
            return self._failure(
                context,
                descriptor,
                "tool.too_large",
                f"工具结果超过 {MAX_TOOL_CHARS} 字符上限",
                hint="缩小范围(更小的 path/pattern/max_results)后重试",
                retryable=True,
                duration_ms=duration_ms,
                sandbox_id=sandbox_id,
            )
        return ExecutionOutcome(
            descriptor=descriptor,
            response=response,
            sandbox_id=sandbox_id,
            patch=patch,
        )

    async def _touch(self, context: ExecutionContext, sandbox_id: str) -> None:
        """滑动 TTL;失败不影响本次结果(只影响沙箱存活时间)。"""

        try:
            await asyncio.wait_for(
                self._sandbox.touch(context.tenant_id, sandbox_id), 5.0
            )
        except (TimeoutError, UpstreamError):
            return
