"""作业路径的日志器、脚本超时、日志回缩预算与提示文案常量。"""

from __future__ import annotations

from common.logging import get_logger

log = get_logger("tool.jobs")

#: 读状态/终止脚本本身的超时(脚本只做 tail 与 kill,远小于工具超时)。
_STATUS_SCRIPT_TIMEOUT_SECONDS = 10.0
#: 整包回缩的预留:信封里除日志还有 job_id/时间/notes 等字段。
_LOG_FIT_RESERVE = 800
#: 单路日志回缩的下限,再小就没有诊断价值了。
_MIN_LOG_CHARS = 200
#: 作业进程"消失但没写退出码"时给模型看的提示。
_NOEXIT_HINT = "进程已消失且未写退出码,可能是沙箱 OOM 或外部终止"
_LOST_HINT = "作业所在沙箱已不可用(可能被回收),作业状态记为 lost"
