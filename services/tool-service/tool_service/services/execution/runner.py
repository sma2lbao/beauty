"""执行主流程:目录查找 → 能力开关 → 风险下限 → 参数校验 → 分派执行。

执行路径按职责拆在 http_path / exec_path / run_code_path / sandbox_path 里,
进程型直跑(exec / run_code)再共享 process_path;ToolRunner 只留主流程、
超时口径与失败封装;一个实例可被并发请求复用(无状态)。
"""

from __future__ import annotations

import json
import time
from typing import Any

from common.errors import NotFoundError, PermissionDeniedError
from common.tools import (
    ExecutorKind,
    ToolDescriptor,
    ToolExecuteResponse,
    ToolKind,
    ToolRisk,
    validate_arguments,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tool_service.constants import DEFAULT_TOOL_TIMEOUT_SECONDS
from tool_service.core.config import Settings
from tool_service.services import catalog
from tool_service.services.connectors import ConnectorGateway
from tool_service.services.execution.arguments import is_available
from tool_service.services.execution.connector_path import ConnectorExecutionMixin
from tool_service.services.execution.exec_path import ExecExecutionMixin
from tool_service.services.execution.http_path import HttpExecutionMixin
from tool_service.services.execution.job_path import JobExecutionMixin
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    _elapsed_ms,
)
from tool_service.services.execution.outcome import (
    failure_outcome,
    normalize_interaction,
)
from tool_service.services.execution.run_code_path import RunCodeExecutionMixin
from tool_service.services.execution.sandbox_path import SandboxExecutionMixin
from tool_service.services.executors.http import HttpToolExecutor
from tool_service.services.sandbox import SandboxProvider


class ToolRunner(
    ExecExecutionMixin,
    RunCodeExecutionMixin,
    SandboxExecutionMixin,
    HttpExecutionMixin,
    ConnectorExecutionMixin,
    JobExecutionMixin,
):
    """按目录分派工具执行;一个实例可被并发请求复用(无状态)。

    job_start / job_output / job_kill 需要独立的短期事务读写后台作业表,
    因此额外持有可选的会话工厂(测试与未装配数据库的场景可留空)。
    """

    _settings: Settings
    _sandbox: SandboxProvider
    _http: HttpToolExecutor | None
    _connector: ConnectorGateway | None
    _session_factory: async_sessionmaker[AsyncSession] | None

    def __init__(
        self,
        *,
        settings: Settings,
        sandbox: SandboxProvider,
        http: HttpToolExecutor | None = None,
        connector: ConnectorGateway | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._settings = settings
        self._sandbox = sandbox
        self._http = http
        self._connector = connector
        self._session_factory = session_factory

    async def execute(
        self,
        context: ExecutionContext,
        name: str,
        arguments: dict[str, Any],
        *,
        internal_arguments: dict[str, Any] | None = None,
    ) -> ExecutionOutcome:
        """执行一次工具调用;返回结果信封或抛调用方错误。

        internal_arguments 是 tool-service 自己算出来的执行期附加参数(如读前置
        记录的 sha256),只并入 helper 请求、不进审计也不出现在工具 schema 里。
        """

        started = time.monotonic()
        descriptor = catalog.find_descriptor(name)
        if descriptor is None and self._connector is not None:
            # 连接器工具不在静态目录里,按账号从 connector-service 目录现查
            # (TTL 缓存);未启用连接器时这里直接跳过。
            descriptor = await self._connector.find(context.account_id, name)
        if descriptor is None or not descriptor.enabled:
            raise NotFoundError("tool.not_found", f"工具 {name} 不存在或未启用")
        if not is_available(descriptor.name, self._settings):
            # 能力开关与目录一致:关闭的工具连执行入口都不存在(设计 §1)。
            raise NotFoundError("tool.not_found", f"工具 {name} 不存在或未启用")
        if (
            descriptor.risk in (ToolRisk.WRITE, ToolRisk.DESTRUCTIVE)
            and not context.approved
        ):
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
        if descriptor.kind is ToolKind.INTERACTION:
            # 交互工具只校验参数并归一化,不执行、不产生副作用;建中断由
            # orchestrator 负责(设计 §8.3)。
            return ExecutionOutcome(
                descriptor=descriptor,
                response=ToolExecuteResponse(
                    ok=True,
                    result=normalize_interaction(arguments),
                    duration_ms=_elapsed_ms(started),
                    tool_call_id=context.tool_call_id,
                ),
            )
        if descriptor.name == "exec":
            # exec 需要真实退出码与 stderr,不走 helper 的 JSON 信封(设计 §6)。
            return await self._run_exec(context, descriptor, arguments, started)
        if descriptor.name == "run_code":
            # 代码经 stdin 交给 python3 -,与 exec 同走沙箱直跑路径(设计 §2)。
            return await self._run_code(context, descriptor, arguments, started)
        if descriptor.name == "job_start":
            # 后台作业:先启动进程再落库,超时/配额/日志见设计 §6。
            return await self._run_job_start(context, descriptor, arguments, started)
        if descriptor.name == "job_output":
            return await self._run_job_output(context, descriptor, arguments, started)
        if descriptor.name == "job_kill":
            return await self._run_job_kill(context, descriptor, arguments, started)
        if descriptor.executor is ExecutorKind.SANDBOX:
            return await self._run_sandbox(
                context, descriptor, arguments, started, internal_arguments
            )
        if descriptor.executor is ExecutorKind.HTTP:
            return await self._run_http(context, descriptor, arguments, started)
        if descriptor.executor is ExecutorKind.CONNECTOR:
            return await self._run_connector(context, descriptor, arguments, started)
        return self._failure(
            context,
            descriptor,
            "tool.not_supported",
            f"工具 {descriptor.name} 由运行时处理,不能直接执行",
            duration_ms=_elapsed_ms(started),
        )

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
        return failure_outcome(
            descriptor,
            context,
            code,
            message,
            hint=hint,
            retryable=retryable,
            duration_ms=duration_ms,
            sandbox_id=sandbox_id,
        )
