"""仓储共用的常量:状态值、事件类型与报告件来源标记(按域拆分后的公共部分)。"""

from __future__ import annotations

QUEUED = "queued"
CONSUMED = "consumed"
TOOL_CALLED_EVENT = "run.tool.called.v1"
#: 交互式提问事件(设计 §10.4):required = 图已挂起等待回答,
#: answered = API/worker 已受理(答案随 resume/job 载荷回到图),timeout = 超时放弃。
QUESTION_REQUIRED_EVENT = "run.question.required.v1"
QUESTION_ANSWERED_EVENT = "run.question.answered.v1"
QUESTION_TIMED_OUT_EVENT = "run.question.timeout.v1"
#: 异步子代理(L2):waiting = 父 run 已挂起等子代理终态(中断载荷带 children),
#: resumed = 子代理全部终态、父 run 恢复执行(载荷带 children 的 id 列表)。
SUBAGENT_WAITING_EVENT = "run.subagent.waiting.v1"
SUBAGENT_RESUMED_EVENT = "run.subagent.resumed.v1"
