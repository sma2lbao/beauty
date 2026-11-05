"""tenancy -> account:账号即隔离边界

- drop organizations(组织产品层拆除),配额上收到 users.quota;
- users:去 tenant_id / role,UNIQUE(subject),加 quota;
- 其余账号私有表 tenant_id -> account_id,索引/唯一约束按 NAMING_CONVENTION 同步改名。

只重命名列/表,不删业务数据;下行重建的 organizations/tenant_id/role 为空壳,
旧租户值不还原(设计文档 docs/design/tenancy-to-account.md §4/§6)。
注意:存量库里若同一 email 存在于多个旧租户,建 UNIQUE(subject) 会失败,
本地开发库建议 npm run dev:reset 重建。

Revision ID: 60b129ed3713
Revises: c4e8a1b7d920
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "60b129ed3713"
down_revision: str | None = "c4e8a1b7d920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (表名, 唯一约束旧名, 唯一约束新名) —— 只有 byok_keys 带 tenant 复合唯一约束
_RENAMED_TABLES: tuple[tuple[str, str | None, str | None], ...] = (
    ("sessions", None, None),
    ("refresh_tokens", None, None),
    ("oauth_identities", None, None),
    ("usage_events", None, None),
    ("api_keys", None, None),
    ("oauth_authorization_codes", None, None),
    ("oauth_tokens", None, None),
    ("byok_keys", "uq_byok_keys_tenant_id", "uq_byok_keys_account_id"),
)


def upgrade() -> None:
    op.drop_table("organizations")

    # users:账号即主体,tenant_id/role 下线,配额改为账号级
    op.drop_index(op.f("ix_users_tenant_id"), table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_tenant_id", type_="unique")
        batch.drop_column("tenant_id")
        batch.drop_column("role")
        batch.add_column(
            sa.Column("quota", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.create_unique_constraint("uq_users_subject", ["subject"])

    for table, old_uq, new_uq in _RENAMED_TABLES:
        op.drop_index(op.f(f"ix_{table}_tenant_id"), table_name=table)
        with op.batch_alter_table(table, recreate="always") as batch:
            if old_uq is not None:
                batch.drop_constraint(old_uq, type_="unique")
            batch.alter_column("tenant_id", new_column_name="account_id")
        if new_uq is not None:
            # SQLite batch 只对「新建约束」做 recreate 时才会把约束写进新表;
            # 与 alter_column 同批会被静默丢弃,故单独一个 batch。
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.create_unique_constraint(new_uq, ["account_id", "provider"])
        op.create_index(
            op.f(f"ix_{table}_account_id"), table, ["account_id"], unique=False
        )


def downgrade() -> None:
    # 下行只恢复结构(空壳),旧租户归属与组织数据不还原。
    for table, old_uq, new_uq in reversed(_RENAMED_TABLES):
        op.drop_index(op.f(f"ix_{table}_account_id"), table_name=table)
        with op.batch_alter_table(table, recreate="always") as batch:
            if new_uq is not None:
                batch.drop_constraint(new_uq, type_="unique")
            batch.alter_column("account_id", new_column_name="tenant_id")
        if old_uq is not None:
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.create_unique_constraint(old_uq, ["tenant_id", "provider"])
        op.create_index(
            op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"], unique=False
        )

    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_subject", type_="unique")
        batch.drop_column("quota")
        batch.add_column(
            sa.Column("role", sa.String(length=16), nullable=False, server_default="member")
        )
        batch.add_column(sa.Column("tenant_id", sa.String(length=32), nullable=True))
        batch.create_unique_constraint("uq_users_tenant_id", ["tenant_id", "subject"])
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"], unique=False)

    op.create_table(
        "organizations",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("quota", sa.JSON(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )