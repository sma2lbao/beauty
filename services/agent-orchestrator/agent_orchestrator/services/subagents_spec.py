"""子代理委派的常量与共享枚举(自 subagents.py 拆出)。

被拆出的兄弟模块(policy/runtime/report)共用这些字面量与状态集合,
集中一处避免各模块各写一份漂开;门面 ``subagents.py`` 继续原样重导出。
"""

from __future__ import annotations

from agent_orchestrator.schemas.runs import RunStatus

#: 工具名/血缘枚举(与契约 §5/§3 一致)。
SPAWN_TOOL_NAME = "spawn_subagent"
DEFAULT_AGENT_NAME = "子代理"

MAX_TITLE_CHARS = 120
MAX_AGENT_NAME_CHARS = 40
MAX_INSTRUCTIONS_CHARS = 4000
MIN_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 1800.0
#: 轮询间隔(契约 §6:"每 1s 轮询")。
POLL_SECONDS = 1.0
#: 血缘上溯深度上限(防环,与 thread 解析同一口径)。
MAX_WALK_DEPTH = 8

TIMEOUT_REASON = "子代理超时"
CANCELLED_REASON = "已取消"
PARENT_CANCELLED_REASON = "父 run 已取消"

_AWAITING_STATUSES = {
    RunStatus.AWAITING_APPROVAL.value,
    RunStatus.AWAITING_CONFIRMATION.value,
    RunStatus.AWAITING_INPUT.value,
}
_TERMINAL_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
    RunStatus.BLOCKED.value,
    RunStatus.CANCELLED.value,
}

# 轮询中止原因(超时/取消的区分只影响 item 的 data.reason 与返回内容)
_ABORT_TIMEOUT = "timeout"
_ABORT_CANCELLED = "cancelled"
_ABORT_PARENT = "parent_cancelled"
