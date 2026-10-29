"""对话仓储:列表/游标分页/归档/工作区归属回填(按域拆分)。"""

from __future__ import annotations

from database.repository import TenantRepository
from sqlalchemy import and_, or_, update

from agent_orchestrator.models.conversations import Conversation


class ConversationRepository(TenantRepository[Conversation]):
    model = Conversation
    not_found_code = "conversation.not_found"
    not_found_message = "对话不存在"
    id_detail_key = "conversation_id"

    async def list_recent(
        self,
        tenant_id: str,
        limit: int = 50,
        *,
        q: str | None = None,
        archived: bool = False,
        before_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[Conversation]:
        """租户内最近活跃在前的对话列表(活跃 = updated_at,消息会顶上去)。

        - 默认排除已归档/已软删;
        - q 按标题模糊搜索;
        - workspace_id 非空时只取该工作区的会话(归属为空的历史行不混进来);
        - before_id 是游标锚点(按 updated_at + id 做 keyset,避免 offset 漂移)。
        """
        criteria: list = [Conversation.deleted_at.is_(None)]
        criteria.append(
            Conversation.archived_at.is_not(None)
            if archived
            else Conversation.archived_at.is_(None)
        )
        if workspace_id is not None:
            criteria.append(Conversation.workspace_id == workspace_id)
        if q and q.strip():
            criteria.append(Conversation.title.ilike(f"%{q.strip()}%"))
        if before_id:
            anchor = await self.get_for_tenant(tenant_id, before_id)
            if anchor is None or anchor.deleted_at is not None:
                return []
            criteria.append(
                or_(
                    Conversation.updated_at < anchor.updated_at,
                    and_(
                        Conversation.updated_at == anchor.updated_at,
                        Conversation.id < anchor.id,
                    ),
                )
            )
        return await self.list_scoped(
            tenant_id,
            *criteria,
            limit=limit,
            order_by=[Conversation.updated_at.desc(), Conversation.id.desc()],
        )

    async def backfill_workspace_id(self, tenant_id: str, workspace_id: str) -> int:
        """把租户内 workspace_id 仍为空的会话一次性归入默认工作区。

        历史数据(本次改造前建的会话,以及工作区集成未启用期间建的会话)没有
        归属,列表接口首次访问时补一次;单条 UPDATE,不逐行处理。返回更新行数。
        """
        result = await self.session.execute(
            update(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.workspace_id.is_(None),
            )
            .values(workspace_id=workspace_id)
        )
        return int(result.rowcount or 0)

    async def require_active_for_tenant(
        self, tenant_id: str, conversation_id: str
    ) -> Conversation:
        """取未删除的对话;已软删与不存在一律 404(不泄漏存在性)。"""
        conversation = await self.get_for_tenant(tenant_id, conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise self._not_found(conversation_id)
        return conversation
