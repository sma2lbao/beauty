"""usage_events + byok_keys(M0-11 本地版)

Revision ID: f1a2b3c4d5e6
Revises: d8f4a1c7e2b9
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "d8f4a1c7e2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
    )
    op.create_index(
        op.f("ix_usage_events_tenant_id"), "usage_events", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_usage_events_run_id"), "usage_events", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_usage_events_total_tokens"),
        "usage_events",
        ["total_tokens"],
        unique=False,
    )
    op.create_table(
        "byok_keys",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=16), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_byok_keys")),
        sa.UniqueConstraint(
            "tenant_id", "provider", name=op.f("uq_byok_keys_tenant_id")
        ),
    )
    op.create_index(
        op.f("ix_byok_keys_tenant_id"), "byok_keys", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_byok_keys_provider"), "byok_keys", ["provider"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_byok_keys_provider"), table_name="byok_keys")
    op.drop_index(op.f("ix_byok_keys_tenant_id"), table_name="byok_keys")
    op.drop_table("byok_keys")
    op.drop_index(op.f("ix_usage_events_total_tokens"), table_name="usage_events")
    op.drop_index(op.f("ix_usage_events_run_id"), table_name="usage_events")
    op.drop_index(op.f("ix_usage_events_tenant_id"), table_name="usage_events")
    op.drop_table("usage_events")
