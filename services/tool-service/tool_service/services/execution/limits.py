"""执行编排的常量:沙箱调用宽限、审计与截断上限、写工具补丁字段。"""

from __future__ import annotations

#: 审计里最多记录的路径条数(避免大 grep 结果把事件撑爆)。
MAX_AUDIT_PATHS = 20
#: 沙箱调用额外宽限:下游自身有超时,客户端只防挂死。
SANDBOX_CALL_GRACE_SECONDS = 10.0
#: 上游错误详情截断长度。
ERROR_DETAIL_CHARS = 300
#: 审计 arguments 列的截断长度。
ARGUMENTS_AUDIT_CHARS = 2000
#: 写工具的服务端补丁字段:只落库,回给编排器/模型的结果里必须剥离。
PATCH_FIELDS = ("patch", "lines_added", "lines_removed", "patch_truncated")
#: exec 的固定 argv 前缀:命令字符串交给 /bin/sh -lc 解释(支持管道与重定向)。
EXEC_SHELL = "/bin/sh"
#: exec 结果 notes 的体积预留(回缩到上限时先扣掉,避免加提示后越界)。
EXEC_NOTES_RESERVE = 600
