"""run 取消探针:graph 侧 CancellationPort 的服务端实现(design/recovery.md §9)。

取消的权威信号是库里的 `run.status = cancelled`(run_cancel.cancel_run 写)。
节点会在安全边界问一次"还该继续吗",本模块把这个问题翻译成一条只读查询:

- 每次都查库(PG READ COMMITTED 下能看到取消方已提交的终态),因此跨进程
  也生效;看到一次 cancelled 之后记住,不再反复查;
- 只读一列 status,不加载 Run 实体,避免与图自己的 flush 抢实体状态;
- 未注入探针时(直跑节点的单测)graph 侧用恒假的 _NullCancellation,
  取消语义不参与。
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Run
from agent_orchestrator.schemas.runs import RunStatus

logger = structlog.get_logger(__name__)


class RunCancellationProbe:
    """节点边界探针:问一次"这个 run 还该继续吗"。"""

    def __init__(self, session: AsyncSession, run_id: str) -> None:
        self._session = session
        self._run_id = run_id
        self._cancelled = False

    async def is_cancelled(self) -> bool:
        if self._cancelled:
            return True
        status = await self._session.scalar(
            select(Run.status).where(Run.id == self._run_id)
        )
        self._cancelled = status == RunStatus.CANCELLED.value
        return self._cancelled


async def settle_cancelled(session: AsyncSession, run: Run, *, stage: str) -> Run:
    """取消在图执行中途生效:刷新内存态、记一条日志,不写 failed。

    终态与 `run.cancelled.v1` 已由取消方在同一时刻写好;这里若再走 _fail_run
    会被 is_superseded 挡掉(那是兜底),显式分支让日志和调用方都读得懂。
    """
    logger.info("run cancelled at node boundary", run_id=run.id, stage=stage)
    await session.refresh(run)
    return run
