"""对话内写操作确认仓储:按租户+对话定位待确认的写调用(§6.7)。

跨租户/跨对话一律 404 —— 确认 id 是前端可枚举的外部标识,不能泄漏存在性;
同一模型调用(tool_call_id)在租户内唯一,确认链路据此做幂等。
"""

from __future__ import annotations

from database.repository import TenantRepository

from agent_orchestrator.models.conversations import ChatToolConfirmation


class ChatToolConfirmationRepository(TenantRepository[ChatToolConfirmation]):
    model = ChatToolConfirmation
    not_found_code = "tool_confirmation.not_found"
    not_found_message = "工具确认不存在"
    id_detail_key = "confirmation_id"

    async def get_for_conversation(
        self, tenant_id: str, conversation_id: str, confirmation_id: str
    ) -> ChatToolConfirmation | None:
        """按租户+对话取确认:对话不匹配与不存在同样返回 None(端点统一 404)。"""
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                ChatToolConfirmation.conversation_id == conversation_id,
                ChatToolConfirmation.id == confirmation_id,
            )
        )

    async def get_for_conversation_for_update(
        self, tenant_id: str, conversation_id: str, confirmation_id: str
    ) -> ChatToolConfirmation | None:
        """同 get_for_conversation,但对确认行加排他锁:并发确认只执行一次。

        Postgres 上 SELECT ... FOR UPDATE 会把第二个确认挡在锁上,提交后读到
        status=confirmed → 409;sqlite 方言忽略 FOR UPDATE,测试库行为不变
        (单写者语义本身已串行)。与 connector-service 的
        ConnectionRepository.require_for_update 同一姿势。
        """
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                ChatToolConfirmation.conversation_id == conversation_id,
                ChatToolConfirmation.id == confirmation_id,
            ).with_for_update()
        )

    async def get_by_tool_call(
        self, tenant_id: str, tool_call_id: str
    ) -> ChatToolConfirmation | None:
        """同一次模型调用已生成的确认(唯一约束的读侧,用于流式重放幂等)。"""
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                ChatToolConfirmation.tool_call_id == tool_call_id,
            )
        )
