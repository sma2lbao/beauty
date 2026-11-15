"""job_start / job_output / job_kill 的执行路径(设计 §6)。

作业进程活在沙箱里,登记行活在 tool-service 自己的库(background_jobs):

- 起步顺序是「先起进程,再落库」,落库失败就尽力把刚起的进程干掉再报错;
- 状态收尾一律走条件 UPDATE(running → 终态),并发的 job_output / job_kill
  只有一方能改,另一方按「已被收尾」读回,不覆盖;
- 沙箱丢失(404 / 不可用)记 lost,而不是把 running 永远挂在那儿;
- 超时是 lazy 的:只在某次 job_output / job_kill 时才发现并收尾,没有后台
  sweeper(设计 §10 的残余风险),长期无人查看的作业会占着并发配额。

作业目录与脚本拼装都在 job_scripts 里(纯函数),这里只管状态机与信封。

包化拆分(W5 出口):constants/snapshot/logs/base/start/output/kill/mixin;
本 __init__ 是门面,保持 tool_service.services.execution.job_path 的原有导入路径不变。
"""

from __future__ import annotations

from .constants import (
    _LOG_FIT_RESERVE,
    _LOST_HINT,
    _MIN_LOG_CHARS,
    _NOEXIT_HINT,
    _STATUS_SCRIPT_TIMEOUT_SECONDS,
    log,
)
from .logs import (
    _fit_job_logs,
    _is_gone,
    _output_notes,
    _stderr_chars,
    _tail_chars,
    _timeout_note,
    _wait_seconds,
)
from .mixin import JobExecutionMixin
from .snapshot import (
    _as_utc,
    _has_pid,
    _iso_utc,
    _JobReading,
    _JobSnapshot,
    _optional_name,
    _reading,
    _snapshot,
)

__all__ = [
    "JobExecutionMixin",
    "_JobReading",
    "_JobSnapshot",
    "_LOST_HINT",
    "_LOG_FIT_RESERVE",
    "_MIN_LOG_CHARS",
    "_NOEXIT_HINT",
    "_STATUS_SCRIPT_TIMEOUT_SECONDS",
    "_as_utc",
    "_fit_job_logs",
    "_has_pid",
    "_is_gone",
    "_iso_utc",
    "_optional_name",
    "_output_notes",
    "_reading",
    "_snapshot",
    "_stderr_chars",
    "_tail_chars",
    "_timeout_note",
    "_wait_seconds",
    "log",
]
