"""异步子代理唤醒(L2):子 run 到终态时把挂起的父 run 送回 job 队列。

放在独立模块是刻意的:graph_result / run_events / run_cancel 都要在"子 run 刚
落终态"时叫它,而它只依赖 jobs 与模型——不 import 图执行模块,避免循环导入。

语义:
- 只叫醒父 run 仍停在 awaiting_subagent 的那一次;父已经继续跑(或已被取消)
  时静默 no-op,子 run 照样在父 run 的过程项里留记录;
- 入队用 dedupe=False:两个子 run 几乎同时终态时各入一条,先到的若发现还有
  子代理在飞会收手,后到的那条负责真正恢复(见 run_subagent_resume)。
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import PARENT_KIND_SUBAGENT, Run
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services.subagents_spec import _TERMINAL_STATUSES

logger = structlog.get_logger(__name__)


async def notify_parent(
    session: AsyncSession, child: Run, *, status: str | None = None
) -> bool:
    """子 run 终态 → 唤醒 awaiting_subagent 的父 run,返回是否真的入了队。"""

    parent_id = child.parent_run_id
    if not parent_id or child.parent_kind != PARENT_KIND_SUBAGENT:
        return False
    state = status or child.status
    if state not in _TERMINAL_STATUSES:
        return False
    parent = await session.get(Run, parent_id)
    if parent is None or parent.account_id != child.account_id:
        return False
    if parent.status != RunStatus.AWAITING_SUBAGENT.value:
        # 父 run 已经继续跑/已取消/已失败:这次唤醒没有意义,静默放弃
        return False
    await jobs_service.enqueue(
        session,
        parent.account_id,
        "subagent",
        parent.id,
        payload={"child_run_id": child.id, "status": state},
        dedupe=False,
    )
    logger.info(
        "subagent wakeup enqueued", parent_run_id=parent.id, child_run_id=child.id
    )
    return True
