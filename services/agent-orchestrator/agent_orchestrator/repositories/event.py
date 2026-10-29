"""run 事件仓储:append-only 流水的 seq 取号与回放(按域拆分)。"""

from __future__ import annotations

from database.repository import TenantRepository
from sqlalchemy import func, select

from agent_orchestrator.models.runs import RunEvent


class RunEventRepository(TenantRepository[RunEvent]):
    """run_events 是 append-only 审计流水(§6.3):只追加、按 seq 读。"""

    model = RunEvent
    not_found_code = "run.not_found"
    not_found_message = "run 不存在"
    id_detail_key = "event_id"

    async def next_seq(self, run_id: str) -> int:
        """该 run 的下一个 seq(每 run 单调递增)。"""
        current = await self.session.scalar(
            select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)
        )
        return int(current or 0) + 1

    async def count_of_type(self, run_id: str, type_: str) -> int:
        """某类事件已落库条数(用于只发增量、避免恢复执行后重复记事件)。"""
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.type == type_)
            )
            or 0
        )

    async def latest_of_type(self, run_id: str, type_: str) -> RunEvent | None:
        """该 run 最近一条某类事件(v1 不建问题表,挂起提问从事件流取,§10.4)。

        按 seq 倒序取第一条即"当前有效载荷";无记录返回 None。
        """
        return await self.session.scalar(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.type == type_)
            .order_by(RunEvent.seq.desc())
            .limit(1)
        )

    async def list_for_run(
        self, tenant_id: str, run_id: str, *, after: int = 0
    ) -> list[RunEvent]:
        """事件回放:按 seq 升序;after 为 SSE 续传游标。"""
        return await self.list_scoped(
            tenant_id,
            RunEvent.run_id == run_id,
            RunEvent.seq > after,
            order_by=[RunEvent.seq],
        )
