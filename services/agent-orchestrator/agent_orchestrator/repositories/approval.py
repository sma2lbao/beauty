"""审批策略/会话授权仓储(按域拆分)。"""

from __future__ import annotations

from database.repository import AccountRepository

from agent_orchestrator.models.approval import (
    ApprovalPolicy,
    ConversationApprovalGrant,
)


class ApprovalPolicyRepository(AccountRepository[ApprovalPolicy]):
    model = ApprovalPolicy
    not_found_code = "approval_policy.not_found"
    not_found_message = "审批策略不存在"
    id_detail_key = "policy_id"

    async def upsert(
        self,
        account_id: str,
        tool: str,
        *,
        action: str,
        arg_name: str = "",
        arg_match: str = "",
        arg_value: str = "",
        flush: bool = True,
    ) -> ApprovalPolicy:
        """按唯一键 (tool, arg_name, arg_match, arg_value) 写一条规则。

        已存在则只改动作(唯一键一致视为同一条规则),避免重复插入撞唯一约束。
        """
        statement = self.scoped(
            account_id,
            ApprovalPolicy.tool == tool,
            ApprovalPolicy.arg_name == arg_name,
            ApprovalPolicy.arg_match == arg_match,
            ApprovalPolicy.arg_value == arg_value,
        )
        row = await self.session.scalar(statement)
        if row is None:
            row = ApprovalPolicy(
                account_id=account_id,
                tool=tool,
                action=action,
                arg_name=arg_name,
                arg_match=arg_match,
                arg_value=arg_value,
            )
            self.session.add(row)
        else:
            row.action = action
        if flush:
            await self.session.flush()
        return row

    async def upsert_auto(
        self, account_id: str, tool: str, *, flush: bool = True
    ) -> ApprovalPolicy:
        """grant=always:把该工具写成账号级 auto 规则(无条件行)。"""
        return await self.upsert(account_id, tool, action="auto", flush=flush)


class ConversationApprovalGrantRepository(AccountRepository[ConversationApprovalGrant]):
    """会话级授权(grant=session):按 (account_id, conversation_id, tool) 唯一。"""

    model = ConversationApprovalGrant
    not_found_code = "approval_grant.not_found"
    not_found_message = "会话授权不存在"
    id_detail_key = "grant_id"

    async def list_for_conversation(
        self, account_id: str, conversation_id: str
    ) -> list[ConversationApprovalGrant]:
        return await self.list_scoped(
            account_id,
            ConversationApprovalGrant.conversation_id == conversation_id,
            order_by=[ConversationApprovalGrant.tool],
        )

    async def get_for_conversation_tool(
        self, account_id: str, conversation_id: str, tool: str
    ) -> ConversationApprovalGrant | None:
        return await self.session.scalar(
            self.scoped(
                account_id,
                ConversationApprovalGrant.conversation_id == conversation_id,
                ConversationApprovalGrant.tool == tool,
            )
        )

    async def upsert(
        self, account_id: str, conversation_id: str, tool: str
    ) -> ConversationApprovalGrant:
        """幂等落一条会话授权(重复确认同一工具只有一行)。"""
        row = await self.get_for_conversation_tool(account_id, conversation_id, tool)
        if row is None:
            row = ConversationApprovalGrant(
                account_id=account_id, conversation_id=conversation_id, tool=tool
            )
            await self.add(row)
        return row

    async def delete_for_conversation(
        self, account_id: str, conversation_id: str
    ) -> int:
        return await self.delete_where(
            account_id,
            ConversationApprovalGrant.conversation_id == conversation_id,
        )
