"""消息附件仓储(按域拆分)。"""

from __future__ import annotations

from database.repository import TenantRepository

from agent_orchestrator.models.conversations import Attachment


class AttachmentRepository(TenantRepository[Attachment]):
    model = Attachment
    not_found_code = "attachment.not_found"
    not_found_message = "附件不存在"
    id_detail_key = "attachment_id"

    async def list_for_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> list[Attachment]:
        return await self.list_scoped(
            tenant_id,
            Attachment.conversation_id == conversation_id,
            order_by=[Attachment.created_at, Attachment.id],
        )

    async def list_for_message(
        self, tenant_id: str, message_id: str
    ) -> list[Attachment]:
        return await self.list_scoped(
            tenant_id,
            Attachment.message_id == message_id,
            order_by=[Attachment.created_at, Attachment.id],
        )

    async def list_for_messages(
        self, tenant_id: str, message_ids: list[str]
    ) -> list[Attachment]:
        """多条消息的附件,按 created_at 倒序(P1-G 多模态:最近的消息先取)。"""
        if not message_ids:
            return []
        return await self.list_scoped(
            tenant_id,
            Attachment.message_id.in_(message_ids),
            order_by=[Attachment.created_at.desc(), Attachment.id.desc()],
        )
