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
