"""tenancy to account：connector-service 列/表/约束/索引改名。

拆掉多租户产品层后的账号边界（account_id = users.id）：tenant_id → account_id，
tenant_apps → account_apps（模型 AccountApp），唯一约束改 (account_id, provider)。
只改名不删数据；SQLite 不支持裸 ALTER，列改名与约束改名统一走 batch_alter_table
（重建表），Postgres 上 batch 退化为逐条 ALTER。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c70941e7a37"
down_revision: str | None = "c4f7b1e93a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNT = sa.String(length=32)


def _drop_account_indexes() -> None:
    op.drop_index("ix_connections_account_id", table_name="connections")
    op.drop_index("ix_oauth_sessions_account_id", table_name="oauth_sessions")
    op.drop_index("ix_account_apps_account_id", table_name="account_apps")
    op.drop_index("ix_outbox_events_account_id", table_name="outbox_events")
    op.drop_index("ix_processed_events_account_id", table_name="processed_events")


def _drop_tenant_indexes() -> None:
    op.drop_index("ix_connections_tenant_id", table_name="connections")
    op.drop_index("ix_oauth_sessions_tenant_id", table_name="oauth_sessions")
    op.drop_index("ix_tenant_apps_tenant_id", table_name="tenant_apps")
    op.drop_index("ix_outbox_events_tenant_id", table_name="outbox_events")
    op.drop_index("ix_processed_events_tenant_id", table_name="processed_events")


def upgrade() -> None:
    _drop_tenant_indexes()
    op.rename_table("tenant_apps", "account_apps")

    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_constraint("uq_connections_tenant_id", type_="unique")
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
    with op.batch_alter_table("oauth_sessions") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
    with op.batch_alter_table("account_apps") as batch_op:
        batch_op.drop_constraint("uq_tenant_apps_tenant_id", type_="unique")
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
    with op.batch_alter_table("outbox_events") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "envelope_tenant_id",
            new_column_name="envelope_account_id",
            existing_type=ACCOUNT,
            existing_nullable=True,
        )
    with op.batch_alter_table("processed_events") as batch_op:
        batch_op.drop_constraint("uq_processed_events_tenant_id", type_="unique")
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )

    op.create_index("ix_connections_account_id", "connections", ["account_id"])
    op.create_index("ix_oauth_sessions_account_id", "oauth_sessions", ["account_id"])
    op.create_index("ix_account_apps_account_id", "account_apps", ["account_id"])
    op.create_index("ix_outbox_events_account_id", "outbox_events", ["account_id"])
    op.create_index("ix_processed_events_account_id", "processed_events", ["account_id"])

    # 唯一约束必须落在 batch 里重建表：SQLite 不支持 ALTER TABLE ADD CONSTRAINT
    with op.batch_alter_table("connections") as batch_op:
        batch_op.create_unique_constraint(
            "uq_connections_account_id", ["account_id", "provider", "account"]
        )
    with op.batch_alter_table("account_apps") as batch_op:
        batch_op.create_unique_constraint(
            "uq_account_apps_account_id", ["account_id", "provider"]
        )
    with op.batch_alter_table("processed_events") as batch_op:
        batch_op.create_unique_constraint(
            "uq_processed_events_account_id", ["account_id", "consumer", "event_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_constraint("uq_connections_account_id", type_="unique")
    with op.batch_alter_table("account_apps") as batch_op:
        batch_op.drop_constraint("uq_account_apps_account_id", type_="unique")
    with op.batch_alter_table("processed_events") as batch_op:
        batch_op.drop_constraint("uq_processed_events_account_id", type_="unique")

    _drop_account_indexes()

    with op.batch_alter_table("connections") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
    with op.batch_alter_table("oauth_sessions") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
    with op.batch_alter_table("account_apps") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
    with op.batch_alter_table("outbox_events") as batch_op:
        batch_op.alter_column(
            "envelope_account_id",
            new_column_name="envelope_tenant_id",
            existing_type=ACCOUNT,
            existing_nullable=True,
        )
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
    with op.batch_alter_table("processed_events") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )

    op.rename_table("account_apps", "tenant_apps")
    op.create_index("ix_connections_tenant_id", "connections", ["tenant_id"])
    op.create_index("ix_oauth_sessions_tenant_id", "oauth_sessions", ["tenant_id"])
    op.create_index("ix_tenant_apps_tenant_id", "tenant_apps", ["tenant_id"])
    op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])
    op.create_index("ix_processed_events_tenant_id", "processed_events", ["tenant_id"])

    with op.batch_alter_table("connections") as batch_op:
        batch_op.create_unique_constraint(
            "uq_connections_tenant_id", ["tenant_id", "provider", "account"]
        )
    with op.batch_alter_table("tenant_apps") as batch_op:
        batch_op.create_unique_constraint(
            "uq_tenant_apps_tenant_id", ["tenant_id", "provider"]
        )
    with op.batch_alter_table("processed_events") as batch_op:
        batch_op.create_unique_constraint(
            "uq_processed_events_tenant_id", ["tenant_id", "consumer", "event_id"]
        )
