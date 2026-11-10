"""admin RBAC:users.role + audit_logs(P0-7)

管理面契约见 docs/design/admin-plane.md:

- users.role(String(16), NOT NULL, server_default 'user'):既有行由数据库默认值
  回填为 user,空库/既有库都能直接 upgrade;角色不进 JWT,管理面每请求读库;
- audit_logs:append-only 审计(id/actor/action/target/detail/请求元数据),
  actor_account_id、action、target_id、created_at 四个索引支撑列表过滤与排序。

Revision ID: f2c7b9d4a1e6
Revises: a9d4f2c8b6e1
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c7b9d4a1e6"
down_revision: str | None = "a9d4f2c8b6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "role", sa.String(length=16), nullable=False, server_default="user"
            )
        )

    op.create_table(
        "audit_logs",
        sa.Column("actor_account_id", sa.String(length=32), nullable=False),
        sa.Column("actor_email", sa.String(length=320), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        op.f("ix_audit_logs_actor_account_id"),
        "audit_logs",
        ["actor_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_target_id"), "audit_logs", ["target_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_target_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_account_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("role")
