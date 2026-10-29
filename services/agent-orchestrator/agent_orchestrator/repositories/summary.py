"""对话摘要仓储:一条对话一份摘要,upsert 幂等(按域拆分)。"""

from __future__ import annotations

from datetime import datetime

from database.repository import TenantRepository

from agent_orchestrator.models.memory import ConversationSummary


class ConversationSummaryRepository(TenantRepository[ConversationSummary]):
    """对话摘要仓储:一条对话一份摘要,upsert 供压缩流程幂等写入。"""

    model = ConversationSummary
    not_found_code = "conversation.summary_not_found"
    not_found_message = "对话摘要不存在"
    id_detail_key = "conversation_id"

    async def get_for_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> ConversationSummary | None:
        """按对话取摘要;没有摘要返回 None(不是 404,调用方据此走全量历史)。"""
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                ConversationSummary.conversation_id == conversation_id,
            )
        )

    async def upsert(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        summary: str,
        covered_until: datetime,
        covered_messages: int,
        tokens_before: int,
        tokens_after: int,
    ) -> ConversationSummary:
        """写入或覆盖摘要(压缩可重复执行,同一对话只保留最新一份)。"""
        existing = await self.get_for_conversation(tenant_id, conversation_id)
        if existing is None:
            existing = ConversationSummary(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                summary=summary,
                covered_until=covered_until,
                covered_messages=covered_messages,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
            )
            self.session.add(existing)
        else:
            existing.summary = summary
            existing.covered_until = covered_until
            existing.covered_messages = covered_messages
            existing.tokens_before = tokens_before
            existing.tokens_after = tokens_after
        await self.session.flush()
        return existing
