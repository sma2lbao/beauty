"""审批策略与授权:账号级工具审批规则 + 会话级临时授权。

默认语义由工具风险等级决定(§6.2):READ/WRITE 自动执行,DANGER 必须人工放行。
账号策略表用于**按账号覆盖**这个默认:

- ``auto``:信任某个工具,连 DANGER 级也不再打断(如账号确认过的 MCP server);
- ``confirm``:把受控写(WRITE)也提到人工确认(合规场景);
- ``deny``:直接禁用某个工具,命中即不执行、把拒绝原因回喂模型。

一条规则 = 工具名(或 ``*``)+ 可选参数条件(`arg_name`/`arg_match`/`arg_value`)。
三列统一用**空串**而不是 NULL 表示"无条件":PG 与 SQLite 对含 NULL 的唯一约束语义
不一致,空串让唯一约束在两种库上行为一致。

``conversation_approval_grants`` 存对话里用户选择"本会话内信任该工具"(grant=session)
后的授权:只对同账号同会话的同名工具生效,动作恒为 auto,删会话时同事务删除。
"""

from __future__ import annotations

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class ApprovalPolicy(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """一条账号级审批规则(工具名或 ``*`` + 动作 + 可选参数条件)。"""

    __tablename__ = "approval_policies"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "tool",
            "arg_name",
            "arg_match",
            "arg_value",
            name="uq_approval_policies_account_tool_args",
        ),
    )

    tool: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(16))
    #: 参数名;为空表示"该工具的所有调用都适用"(无条件规则)。
    arg_name: Mapped[str] = mapped_column(String(64), default="", server_default="")
    #: 参数匹配方式:exact / prefix / glob(只在 arg_name 非空时有意义)。
    arg_match: Mapped[str] = mapped_column(String(16), default="", server_default="")
    #: 匹配值(exact 全等 / prefix 前缀 / glob 通配)。
    arg_value: Mapped[str] = mapped_column(String(512), default="", server_default="")


class ConversationApprovalGrant(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """会话级临时授权:同会话内再遇到该工具直接自动放行(grant=session 落的库)。"""

    __tablename__ = "conversation_approval_grants"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "conversation_id",
            "tool",
            name="uq_conversation_grants_account_conversation_tool",
        ),
    )

    conversation_id: Mapped[str] = mapped_column(String(36))
    tool: Mapped[str] = mapped_column(String(120))
