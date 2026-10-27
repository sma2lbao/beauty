"""run 服务门面:对外签名稳定,实现按职责拆在同包各模块。

拆分(R-28,行为零变化):
- run_lifecycle:状态机迁移表、创建/列表、指令队列、多轮上下文、状态守卫
- run_events:事件写入(seq 单调)、正文增量攒批、回放查询、失败终态投影
- run_cancel:取消(非终态→cancelled,排队 job 一并作废;协作式收手)
- run_artifacts:交付物登记、工具外链抓取、工作区镜像与取件
- run_quota:准入闸(fail-open)与用量边界观测
- run_execution:图调用边界(端口/增量注入)、审批编排与结果投影
- run_stream:SSE 帧生成、断线续传(Last-Event-ID)与 keep-alive
- run_steps:步骤视图投影(run.step.v1 → GET /runs/{id}/steps,零迁移,07 S1)

本模块只做 re-export:调用方(api/v1/runs.py、jobs_worker、conversations、
tests)一律经此取服务函数,拆分不改变任何对外名字与签名。
"""

from __future__ import annotations

from agent_orchestrator.services.run_artifacts import (
    _persist_artifact,
    get_artifact,
)
from agent_orchestrator.services.run_cancel import cancel_run
from agent_orchestrator.services.run_events import (
    append_event,
    list_events,
)
from agent_orchestrator.services.run_execution import (
    answer_run,
    approve_run,
    confirm_run,
    plan_run,
    reject_run,
    resume_run,
    retry_run,
    update_plan,
)
from agent_orchestrator.services.run_lifecycle import (
    create_run,
    list_instructions,
    list_runs,
    queue_instruction,
    require_run,
)
from agent_orchestrator.services.run_steps import list_steps
from agent_orchestrator.services.todos import list_todos

__all__ = [
    "_persist_artifact",
    "answer_run",
    "append_event",
    "approve_run",
    "cancel_run",
    "confirm_run",
    "create_run",
    "get_artifact",
    "list_events",
    "list_instructions",
    "list_runs",
    "list_steps",
    "list_todos",
    "plan_run",
    "queue_instruction",
    "reject_run",
    "require_run",
    "resume_run",
    "retry_run",
    "update_plan",
]
