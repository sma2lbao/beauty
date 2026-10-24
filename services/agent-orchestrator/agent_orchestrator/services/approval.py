"""审批策略引擎(P0-4):把"要不要人工确认"从布尔判断变成租户可配的规则。

判据收口成一条纯函数 :func:`decide`,节点把结果当状态用(节点仍是纯函数):

1. 精确匹配的租户规则(``tool == 工具名``)优先;
2. 其次是通配规则 ``*``;
3. 都没命中才回落到**风险等级默认值**(DANGER → confirm,READ/WRITE → auto),
   与引入策略表之前的行为完全一致。

命中 ``deny`` 时不执行工具,把拒绝原因作为工具结果回喂模型——它能看懂"这个动作
被策略禁止",然后换个做法,而不是让整个 run 失败。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.approval import ApprovalPolicy
from agent_orchestrator.repositories import ApprovalPolicyRepository
from agent_orchestrator.services.tools import ToolRisk

#: 通配规则:匹配任意工具名。
WILDCARD = "*"

#: 规则动作(P0-4):自动放行 / 人工确认 / 直接拒绝。
VALID_ACTIONS = ("auto", "confirm", "deny")


class ApprovalAction(StrEnum):
    """策略求值结果。"""

    AUTO = "auto"
    CONFIRM = "confirm"
    DENY = "deny"


def decide(
    policies: Sequence[Mapping[str, str]] | None,
    tool: str,
    risk: ToolRisk,
) -> ApprovalAction:
    """求一个工具本次调用的审批动作(纯函数,可单测)。"""
    rows = policies or []
    row = next((item for item in rows if item.get("tool") == tool), None)
    if row is None:
        row = next((item for item in rows if item.get("tool") == WILDCARD), None)
    if row is not None:
        action = str(row.get("action") or "").lower()
        if action in VALID_ACTIONS:
            return ApprovalAction(action)
    return ApprovalAction.CONFIRM if risk is ToolRisk.DANGER else ApprovalAction.AUTO


async def load_policies(session: AsyncSession, tenant_id: str) -> list[dict[str, str]]:
    """读该租户的策略快照(进图状态,节点不再碰数据库)。"""
    rows = await ApprovalPolicyRepository(session).list_scoped(
        tenant_id, order_by=[ApprovalPolicy.tool]
    )
    return [{"tool": row.tool, "action": row.action} for row in rows]


async def replace_policies(
    session: AsyncSession, tenant_id: str, items: Sequence[Mapping[str, str]]
) -> list[dict[str, str]]:
    """整表替换该租户的策略(策略是"当前意图",不做逐条增量)。"""
    repo = ApprovalPolicyRepository(session)
    await repo.delete_all_for_tenant(tenant_id)
    for item in items:
        session.add(
            ApprovalPolicy(
                tenant_id=tenant_id,
                tool=str(item["tool"]),
                action=str(item["action"]),
            )
        )
    await session.flush()
    return await load_policies(session, tenant_id)
