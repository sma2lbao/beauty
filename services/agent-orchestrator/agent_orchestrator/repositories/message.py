"""对话消息仓储:回放/计数/多轮上下文(按域拆分)。"""

from __future__ import annotations

from datetime import datetime

from database.repository import TenantRepository

from agent_orchestrator.models.conversations import ChatMessage


class MessageRepository(TenantRepository[ChatMessage]):
    model = ChatMessage
    not_found_code = "conversation.not_found"
    not_found_message = "消息不存在"
    id_detail_key = "message_id"

    async def list_for_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> list[ChatMessage]:
        """对话消息回放:按时间升序(id 兜底保证顺序确定)。"""
        return await self.list_scoped(
            tenant_id,
            ChatMessage.conversation_id == conversation_id,
            order_by=[ChatMessage.created_at, ChatMessage.id],
        )

    async def has_any_message(self, tenant_id: str, conversation_id: str) -> bool:
        """对话里是否已有消息(判定首条消息 → 自动定题)。"""
        return await self.exists_for_tenant(
            tenant_id, ChatMessage.conversation_id == conversation_id
        )

    async def count_for_conversation(self, tenant_id: str, conversation_id: str) -> int:
        """对话消息条数:自动抽取按"累计 N 条"节流时用来判断是否该再抽一次。"""
        return await self.count(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.conversation_id == conversation_id,
        )

    async def list_history(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        before: datetime,
        exclude_run_id: str,
    ) -> list[ChatMessage]:
        """多轮上下文:run 建立之前的对话消息,排除本 run 自己的消息。

        触发本 run 的用户消息(先落库、再建 run)会被两个条件同时排除:
        created_at 早于 run 是主判据,run_id 相同是兜底——并发到达的
        追加指令(建 run 之后落库)两条都能拦住,不会重复进上下文。
        """
        return await self.list_scoped(
            tenant_id,
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.created_at < before,
            (ChatMessage.run_id != exclude_run_id) | (ChatMessage.run_id.is_(None)),
            order_by=[ChatMessage.created_at, ChatMessage.id],
        )

    async def list_before(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        before: datetime,
        exclude_id: str,
    ) -> list[ChatMessage]:
        """直接对话的多轮上下文:这条消息之前的全部轮次(不分 run)。

        与 list_history 的区别:那里按 run 排除(触发消息与追加指令不进上下文),
        这里只排除消息自身——闲聊要看得到此前所有往来。
        """
        return await self.list_scoped(
            tenant_id,
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.created_at < before,
            ChatMessage.id != exclude_id,
            order_by=[ChatMessage.created_at, ChatMessage.id],
        )
