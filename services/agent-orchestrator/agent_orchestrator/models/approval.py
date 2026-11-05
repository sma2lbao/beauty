"""审批策略(P0-4):账号级的工具审批规则表。

默认语义由工具风险等级决定(§6.2):READ/WRITE 自动执行,DANGER 必须人工放行。
本表用于**按账号覆盖**这个默认:

- ``auto``:信任某个工具,连 DANGER 级也不再打断(如账号确认过的 MCP server);
- ``confirm``:把受控写(WRITE)也提到人工确认(合规场景);
- ``deny``:直接禁用某个工具,命中即不执行、把拒绝原因回喂模型。

``tool`` 存工具名,或 ``*`` 匹配全部工具(用于"本账号一律要确认"这类兜底)。
同一账号内 (tool) 唯一;规则为空表时行为与引入本表之前完全一致。
"""

from __future__ import annotations

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class ApprovalPolicy(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """一条账号级审批规则(工具名或 ``*`` + 动作)。"""

    __tablename__ = "approval_policies"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "tool", name="uq_approval_policies_account_tool"
        ),
    )

    tool: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(16))
