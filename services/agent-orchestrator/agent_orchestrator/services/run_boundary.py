"""节点边界提交(design/recovery.md §9):让取消与崩溃恢复看得见进展。

图的执行方如果用同一个会话整段跑完才提交,取消方(另一个会话/进程)在库里
写 run.status=cancelled 时就拿不到写锁(SQLite 库级锁;Postgres 下若执行方
改过同一行也会阻塞),"取消"只能等图自己结束才生效。边界提交把每两个节点
之间的变化(run 状态、事件、过程项)落库,取消探针随后读到最新状态。

这与 checkpoint 的分工:checkpoint 记图走到哪一步(可续跑),边界提交记
"这些副作用已确认发生"(事件/审计可见),两者都在节点之间发生。
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class SessionBoundary:
    """graph.ports.BoundaryPort 的 services 实现:节点开工前提交当前会话。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def before_node(self) -> None:
        # expire_on_commit=False:提交不会让 run 等 ORM 属性过期,后续读取
        # 仍是对象上的值,不需要额外 refresh
        await self._session.commit()
