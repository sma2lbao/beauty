"""单 run 写配额与调用次数上限(超限是模型可见的工具错误,不是 HTTP 错误)。

配额按 run 维度统计 tool_calls 里已成功的写工具记录(bytes_written 累计)。
tool-service 是执行面,配额只做兜底;真正的成本配额在 orchestrator/usage 侧。
"""

from __future__ import annotations

from common.tools import ToolError


def check_total_calls(*, used_calls: int, limit: int) -> ToolError | None:
    """单 run 工具调用总次数上限(防失控循环)。"""

    if used_calls >= limit:
        return ToolError(
            code="tool.budget_exceeded",
            message=f"单 run 工具调用次数已达上限 {limit} 次",
            hint="请在新任务或新会话中继续,或缩小任务范围",
            retryable=False,
        )
    return None


def check_write_quota(
    *,
    used_writes: int,
    used_bytes: int,
    incoming_bytes: int,
    max_writes: int,
    max_bytes: int,
) -> ToolError | None:
    """单 run 写次数/写入字节上限(只对 write/edit 生效)。"""

    if used_writes >= max_writes:
        return ToolError(
            code="tool.budget_exceeded",
            message=f"单 run 写操作次数已达上限 {max_writes} 次",
            hint="请检查是否在重复写入,或把批量修改合并为一次 edit(replace_all=true)",
            retryable=False,
        )
    if used_bytes >= max_bytes or used_bytes + max(incoming_bytes, 0) > max_bytes:
        return ToolError(
            code="tool.budget_exceeded",
            message=f"单 run 写入字节数已达上限 {max_bytes} 字节",
            hint=f"已写入 {used_bytes} 字节;请缩小文件或拆分为多个 run",
            retryable=False,
        )
    return None
