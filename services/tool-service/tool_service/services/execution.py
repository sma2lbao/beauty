"""工具执行编排:目录查询 → 风险下限 → 参数校验 → 执行 → 结果信封。

执行错误是模型可见的数据(HTTP 200 + ok=false),只有鉴权、未知工具、
需要确认、上游不可用这类调用方错误才走 API 错误。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, replace
from typing import Any

from common.errors import NotFoundError, PermissionDeniedError, UpstreamError
from common.tools import (
    ExecutorKind,
    ToolDescriptor,
    ToolError,
    ToolExecuteResponse,
    ToolRisk,
    validate_arguments,
)

from tool_service.constants import DEFAULT_TOOL_TIMEOUT_SECONDS, MAX_TOOL_CHARS
from tool_service.core.config import Settings
from tool_service.services.catalog import find_descriptor
from tool_service.services.helpers import build_request, helper_argv
from tool_service.services.sandbox import SandboxProvider, scope_key_for

#: 审计里最多记录的路径条数(避免大 grep 结果把事件撑爆)。
MAX_AUDIT_PATHS = 20
#: 沙箱调用额外宽限:下游自身有超时,客户端只防挂死。
SANDBOX_CALL_GRACE_SECONDS = 10.0
#: 上游错误详情截断长度。
ERROR_DETAIL_CHARS = 300
#: 审计 arguments 列的截断长度。
ARGUMENTS_AUDIT_CHARS = 2000


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """一次工具执行的调用方上下文(租户来自 JWT,其余来自请求体)。"""

    tenant_id: str
    tool_call_id: str
    run_id: str | None = None
    conversation_id: str | None = None
    workspace_id: str | None = None
    sandbox_scope: str | None = None
    timeout_seconds: float | None = None
    approved: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """执行结果 + 可落审计的附加信息。"""

    descriptor: ToolDescriptor
    response: ToolExecuteResponse
    sandbox_id: str | None = None


def arguments_digest(arguments: dict[str, Any]) -> str:
    """参数摘要:用于幂等键冲突判定(排序键 + 紧凑分隔符)。"""

    canonical = json.dumps(
        arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolRunner:
    """按目录分派工具执行;一个实例可被并发请求复用(无状态)。"""

    def __init__(self, *, settings: Settings, sandbox: SandboxProvider) -> None:
        self._settings = settings
        self._sandbox = sandbox

    async def execute(
        self, context: ExecutionContext, name: str, arguments: dict[str, Any]
    ) -> ExecutionOutcome:
        """执行一次工具调用;返回结果信封或抛调用方错误。"""

        started = time.monotonic()
        descriptor = find_descriptor(name)
        if descriptor is None or not descriptor.enabled:
            raise NotFoundError("tool.not_found", f"工具 {name} 不存在或未启用")
        if descriptor.risk is ToolRisk.DESTRUCTIVE and not context.approved:
            raise PermissionDeniedError(
                "tool.confirmation_required",
                f"工具 {descriptor.name} 需要人工确认后才能执行",
            )
        errors = validate_arguments(descriptor.input_schema, arguments)
        if errors:
            error = errors[0]
            return self._failure(
                context,
                descriptor,
                error.code,
                error.message,
                hint=error.hint,
                duration_ms=_elapsed_ms(started),
            )
        if descriptor.executor is ExecutorKind.SANDBOX:
            return await self._run_sandbox(context, descriptor, arguments, started)
        if descriptor.executor is ExecutorKind.HTTP:
            return self._failure(
                context,
                descriptor,
                "tool.executor_unavailable",
                "该工具的下游执行器尚未接线",
                hint="http 执行器在 P1 落地",
                duration_ms=_elapsed_ms(started),
            )
        return self._failure(
            context,
            descriptor,
            "tool.not_supported",
            f"工具 {descriptor.name} 由运行时处理,不能直接执行",
            duration_ms=_elapsed_ms(started),
        )

    # ---- 沙箱执行 -------------------------------------------------------

    async def _run_sandbox(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        started: float,
    ) -> ExecutionOutcome:
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
                timeout + SANDBOX_CALL_GRACE_SECONDS,
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
                    stdin=build_request(descriptor.name, arguments),
                    timeout_seconds=max(1, int(timeout)),
                ),
                timeout + SANDBOX_CALL_GRACE_SECONDS,
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
        )

    async def _touch(self, context: ExecutionContext, sandbox_id: str) -> None:
        """滑动 TTL;失败不影响本次结果(只影响沙箱存活时间)。"""

        try:
            await asyncio.wait_for(
                self._sandbox.touch(context.tenant_id, sandbox_id), 5.0
            )
        except (TimeoutError, UpstreamError):
            return

    def _timeout_seconds(
        self, context: ExecutionContext, descriptor: ToolDescriptor
    ) -> float:
        """执行超时:目录声明上限、全局配置与请求覆盖三者取小。"""

        configured = float(
            self._settings.tool_timeout_seconds or DEFAULT_TOOL_TIMEOUT_SECONDS
        )
        base = float(descriptor.timeout_seconds or configured)
        if context.timeout_seconds is not None:
            base = min(base, float(context.timeout_seconds))
        return max(1.0, base)

    # ---- 结果与审计 -----------------------------------------------------

    @staticmethod
    def _result_chars(response: ToolExecuteResponse) -> int:
        if not response.ok or response.result is None:
            return 0
        return len(json.dumps(response.result, ensure_ascii=False))

    def _failure(
        self,
        context: ExecutionContext,
        descriptor: ToolDescriptor,
        code: str,
        message: str,
        *,
        hint: str = "",
        retryable: bool = False,
        duration_ms: int = 0,
        sandbox_id: str | None = None,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            descriptor=descriptor,
            response=ToolExecuteResponse(
                ok=False,
                error=ToolError(
                    code=code,
                    message=message,
                    hint=hint,
                    retryable=retryable,
                ),
                duration_ms=duration_ms,
                tool_call_id=context.tool_call_id,
            ),
            sandbox_id=sandbox_id,
        )


def audit_columns(
    outcome: ExecutionOutcome,
    context: ExecutionContext,
    arguments: dict[str, Any],
    *,
    decision: str,
) -> dict[str, Any]:
    """把执行结果映射成 tool_calls 表的列(调用方负责 insert)。"""

    response = outcome.response
    result = response.result or {}
    error = response.error
    return {
        "tool_name": outcome.descriptor.name,
        "tool_version": outcome.descriptor.version,
        "risk": str(outcome.descriptor.risk),
        "decision": decision,
        "arguments": json.dumps(arguments, ensure_ascii=False, sort_keys=True)[
            :ARGUMENTS_AUDIT_CHARS
        ],
        "arguments_sha256": arguments_digest(arguments),
        "ok": response.ok,
        "error_code": error.code if error is not None else None,
        "duration_ms": response.duration_ms,
        "result_chars": _result_chars_str(result),
        "truncated": response.truncated,
        "paths": json.dumps(_result_paths(result), ensure_ascii=False),
        "sha256_before": _optional_str(result.get("sha256_before")),
        "sha256_after": _optional_str(result.get("sha256_after")),
        "sandbox_id": outcome.sandbox_id,
    }


def _result_chars_str(result: dict[str, Any]) -> int:
    return len(json.dumps(result, ensure_ascii=False)) if result else 0


def _result_paths(result: dict[str, Any]) -> list[str]:
    """从结果里提取文件路径(审计展示用,最多 MAX_AUDIT_PATHS 条)。"""

    raw: list[str] = []
    path = result.get("path")
    if isinstance(path, str):
        raw.append(path)
    for key in ("entries", "matches"):
        values = result.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, str):
                raw.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                raw.append(item["path"])
            if len(raw) >= MAX_AUDIT_PATHS:
                break
    return raw[:MAX_AUDIT_PATHS]


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
