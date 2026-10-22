"""conversations/runs:工作区归属 workspace_id

Revision ID: e7c2a4b8d1f3
Revises: b6f1c8d92a47
Create Date: 2026-10-22

对话必须落在工作区里:conversations 记录所属工作区,由对话消息触发的 run
继承同一个归属。两列都是跨服务弱引用(workspace-service 的实体不在本库),
所以不建外键——与 runs.conversation_id 的既有惯例一致。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7c2a4b8d1f3"
down_revision: str | None = "b6f1c8d92a47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 存量会话的 workspace_id 为空:列表接口首次访问时按租户默认工作区懒回填
    op.add_column(
        "conversations",
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "workspace_id")
    op.drop_column("conversations", "workspace_id")
