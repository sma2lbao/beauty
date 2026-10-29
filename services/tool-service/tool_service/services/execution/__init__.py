"""工具执行编排:目录查询 → 风险下限 → 参数校验 → 执行 → 结果信封。

按职责拆成多个模块,包门面重导出原 `execution.py` 的全部公开名与测试引用的
私有名,保持 `from tool_service.services.execution import X` 的路径不变:

- `limits`:沙箱调用宽限、审计/截断上限与写工具补丁字段等常量;
- `models`:ExecutionContext / PatchAudit / ExecutionOutcome 与耗时口径;
- `arguments`:参数摘要、能力开关、exec 的 cwd 夹取;
- `exec_output`:exec 输出裁剪、整包回缩与沙箱超时识别;
- `outcome`:失败信封、交互工具归一、tool_calls 审计列映射;
- `runner`:执行主流程与调度、超时口径、失败封装;
- `http_path` / `exec_path` / `sandbox_path`:三条执行路径。

执行错误是模型可见的数据(HTTP 200 + ok=false),只有鉴权、未知工具、
需要确认、上游不可用这类调用方错误才走 API 错误。
"""

from __future__ import annotations

from tool_service.services.execution.arguments import (
    arguments_digest,
    is_available,
    resolve_exec_cwd,
)
from tool_service.services.execution.exec_output import (
    _clip_exec_stream,
    _fit_exec_result,
    _is_exec_timeout,
    _payload_chars,
)
from tool_service.services.execution.limits import (
    ARGUMENTS_AUDIT_CHARS,
    ERROR_DETAIL_CHARS,
    EXEC_NOTES_RESERVE,
    EXEC_SHELL,
    MAX_AUDIT_PATHS,
    PATCH_FIELDS,
    SANDBOX_CALL_GRACE_SECONDS,
)
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    PatchAudit,
    _elapsed_ms,
)
from tool_service.services.execution.outcome import (
    _bytes_written,
    _optional_int,
    _optional_str,
    _result_chars_str,
    _result_paths,
    _split_patch,
    audit_columns,
    failure_outcome,
    normalize_interaction,
)
from tool_service.services.execution.runner import ToolRunner

__all__ = [
    # 常量
    "ARGUMENTS_AUDIT_CHARS",
    "ERROR_DETAIL_CHARS",
    "EXEC_NOTES_RESERVE",
    "EXEC_SHELL",
    "MAX_AUDIT_PATHS",
    "PATCH_FIELDS",
    "SANDBOX_CALL_GRACE_SECONDS",
    # 模型
    "ExecutionContext",
    "ExecutionOutcome",
    "PatchAudit",
    # 参数与能力
    "arguments_digest",
    "is_available",
    "resolve_exec_cwd",
    # 执行主流程
    "ToolRunner",
    # 结果与审计
    "audit_columns",
    "failure_outcome",
    "normalize_interaction",
    # 私有名:原 execution.py 内部使用,测试/审计映射直接引用,一并重导出
    "_bytes_written",
    "_clip_exec_stream",
    "_elapsed_ms",
    "_fit_exec_result",
    "_is_exec_timeout",
    "_optional_int",
    "_optional_str",
    "_payload_chars",
    "_result_chars_str",
    "_result_paths",
    "_split_patch",
]
