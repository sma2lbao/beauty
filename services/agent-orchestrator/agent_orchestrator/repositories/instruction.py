"""待消费指令仓储(按域拆分)。"""

from __future__ import annotations

from database.repository import TenantRepository

from agent_orchestrator.models.runs import PendingInstruction
from agent_orchestrator.repositories.constants import QUEUED


class InstructionRepository(TenantRepository[PendingInstruction]):
    model = PendingInstruction
    not_found_code = "run.not_found"
    not_found_message = "指令不存在"
    id_detail_key = "instruction_id"

    async def list_for_run(
        self, tenant_id: str, run_id: str
    ) -> list[PendingInstruction]:
        """该 run 的指令队列(queued/consumed 都返回,控制台据此展示)。"""
        return await self.list_scoped(
            tenant_id,
            PendingInstruction.run_id == run_id,
            order_by=[PendingInstruction.created_at],
        )

    async def list_queued(
        self, tenant_id: str, run_id: str
    ) -> list[PendingInstruction]:
        """待消费指令(按入队顺序)。"""
        return await self.list_scoped(
            tenant_id,
            PendingInstruction.run_id == run_id,
            PendingInstruction.status == QUEUED,
            order_by=[PendingInstruction.created_at],
        )
