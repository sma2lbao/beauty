"""run 取消探针:graph 侧 CancellationPort 的服务端实现(design/recovery.md §9)。

取消的权威信号是库里的 `run.status = cancelled`(run_cancel.cancel_run 写)。
节点会在安全边界问一次"还该继续吗",S6 起执行期的外呼也会按轮询问它,
本模块把这个问题翻译成一条只读查询:

- 每次都查库(PG READ COMMITTED 下能看到取消方已提交的终态),因此跨进程
  也生效;看到一次 cancelled 之后记住,不再反复查;
- 只读一列 status,不加载 Run 实体,避免与图自己的 flush 抢实体状态;
- **用独立连接读**(S6):执行期的轮询与执行本身并行,若复用执行会话,
  每次 SELECT 都会在执行会话上留下一个未结束的读事务,SQLite 下读事务
  持有 SHARED 锁、取消方持有 RESERVED,两边的 commit 互相等着对方放锁,
  直接撞成 `database is locked`;独立短连接读完即关,执行会话的事务边界
  保持原样;
- 探针读失败(锁冲突、连接抖动)只记 warning 并当作"尚未取消",取消的
  权威信号下一次轮询/下一个节点边界还会再看,不能因为读失败把 run 打死;
- 未注入探针时(直跑节点的单测)graph 侧用恒假的 _NullCancellation,
  取消语义不参与。
"""

from __future__ import annotations

import structlog
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from agent_orchestrator.models.runs import Run
from agent_orchestrator.schemas.runs import RunStatus

logger = structlog.get_logger(__name__)


def _status_query(run_id: str) -> Select[tuple[str]]:
    return select(Run.status).where(Run.id == run_id)


def _probe_bind(session: AsyncSession) -> AsyncEngine | None:
    """取执行会话背后的引擎:探针要另开连接,不能蹭执行会话的事务。"""
    bind = getattr(session, "bind", None)
    return bind if isinstance(bind, AsyncEngine) else None


class RunCancellationProbe:
    """节点边界/执行期探针:问一次"这个 run 还该继续吗"。"""

    def __init__(self, session: AsyncSession, run_id: str) -> None:
        self._session = session
        self._bind = _probe_bind(session)
        self._run_id = run_id
        self._cancelled = False

    async def is_cancelled(self) -> bool:
        if self._cancelled:
            return True
        status = await self._read_status()
        self._cancelled = status == RunStatus.CANCELLED.value
        return self._cancelled

    async def _read_status(self) -> str | None:
        """读一次状态;拿不到引擎(假会话)时退回执行会话,读失败视作未取消。"""
        query = _status_query(self._run_id)
        if self._bind is None:
            try:
                return await self._session.scalar(query)
            except Exception as exc:  # noqa: BLE001 - 探针读失败不阻断执行
                logger.warning(
                    "cancel probe failed", run_id=self._run_id, error=str(exc)
                )
                return None
        try:
            async with AsyncSession(
                bind=self._bind, expire_on_commit=False
            ) as probe_session:
                return await probe_session.scalar(query)
        except Exception as exc:  # noqa: BLE001 - 同上
            logger.warning("cancel probe failed", run_id=self._run_id, error=str(exc))
            return None


async def settle_cancelled(session: AsyncSession, run: Run, *, stage: str) -> Run:
    """取消在图执行中途生效:刷新内存态、记一条日志,不写 failed。

    终态与 `run.cancelled.v1` 已由取消方在同一时刻写好;这里若再走 _fail_run
    会被 is_superseded 挡掉(那是兜底),显式分支让日志和调用方都读得懂。
    """
    logger.info("run cancelled at node boundary", run_id=run.id, stage=stage)
    await session.refresh(run)
    return run
