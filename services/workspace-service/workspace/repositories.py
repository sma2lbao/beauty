"""工作区域仓储:账号过滤与 404 语义在这里声明一次。"""

from __future__ import annotations

from datetime import datetime, timedelta

from database.repository import AccountRepository
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.models.sandbox import SandboxInstance
from workspace.models.workspace import Workspace, WorkspaceArtifact

#: 回收器认的"占用中"状态:creating 也可能是过期无人接管的孤儿行。
REAPABLE_STATUSES = ("running", "creating")


def _expired_statement(now: datetime, *, limit: int) -> Select[tuple[SandboxInstance]]:
    """过期沙箱候选:两类占用中状态,按到期先后;调用方决定是否加锁。"""
    return (
        select(SandboxInstance)
        .where(
            SandboxInstance.status.in_(REAPABLE_STATUSES),
            SandboxInstance.expires_at < now,
        )
        .order_by(SandboxInstance.expires_at)
        .limit(limit)
    )


def _claim_statement(
    now: datetime, *, limit: int, lock: bool
) -> Select[tuple[SandboxInstance]]:
    """领取语句:PG 追加 FOR UPDATE SKIP LOCKED,SQLite 没有行锁。

    多实例部署时,被别的副本锁住的行会被直接跳过,两个 reaper 不会领到同一批;
    SQLite 是单写者场景,靠"领取即在同事务内推后 expires_at"互斥。
    """
    statement = _expired_statement(now, limit=limit)
    if not lock:
        return statement
    return statement.with_for_update(skip_locked=True)


def _is_postgres(session: AsyncSession) -> bool:
    """领取策略按方言分叉:PG 用行锁跳过,SQLite 没有 FOR UPDATE。"""
    bind = session.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


class WorkspaceRepository(AccountRepository[Workspace]):
    model = Workspace
    not_found_code = "workspace.not_found"
    not_found_message = "工作区不存在"
    id_detail_key = "workspace_id"


class ArtifactRepository(AccountRepository[WorkspaceArtifact]):
    model = WorkspaceArtifact
    not_found_code = "artifact.not_found"
    not_found_message = "交付物不存在"
    id_detail_key = "artifact_id"


class SandboxRepository(AccountRepository[SandboxInstance]):
    model = SandboxInstance
    not_found_code = "sandbox.not_found"
    not_found_message = "沙箱不存在"
    id_detail_key = "sandbox_id"


class CrossAccountSandboxRepository:
    """回收器专用:跨账号查询/领取过期沙箱(后台任务没有"当前账号")。

    仅用于 `reap_expired`;HTTP 路径一律走 `SandboxRepository`(账号隔离)。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_expired(
        self, now: datetime, *, limit: int = 100
    ) -> list[SandboxInstance]:
        """只读地列出已过期沙箱(不领取、不推后 expires_at)。

        仅用于观察/测试;真正的回收必须走 `claim_expired`,否则多副本会重复回收。
        """
        return list(await self.session.scalars(_expired_statement(now, limit=limit)))

    async def claim_expired(
        self, now: datetime, *, limit: int = 100, lease_seconds: int = 300
    ) -> list[SandboxInstance]:
        """原子领取过期沙箱:命中行的 expires_at 在同一短事务内推后到租约末端。

        - PostgreSQL:`FOR UPDATE SKIP LOCKED`,多副本各领不同的行、互不等待;
        - SQLite:没有行锁,单写者部署下"领取即在同事务内推后 expires_at"
          已足以让下一个事务看不到这批行(写锁串行化)。

        返回领取到的行;调用方必须在**事务外**做 provider.destroy,再用短事务
        回写结果,不能在数据库事务里做外部调用。租约(默认 300s)是崩溃兜底:
        领取后进程死掉,expires_at 到点后下一轮会重新领取(因此 destroy 必须
        按幂等对待)。
        """
        statement = _claim_statement(now, limit=limit, lock=_is_postgres(self.session))
        records = list(await self.session.scalars(statement))
        if not records:
            return []
        lease_until = now + timedelta(seconds=lease_seconds)
        for record in records:
            record.expires_at = lease_until
        await self.session.flush()
        return records
