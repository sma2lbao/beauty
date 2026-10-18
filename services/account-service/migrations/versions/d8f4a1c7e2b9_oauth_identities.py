"""社交登录:oauth_identities(provider + subject 唯一)

Revision ID: d8f4a1c7e2b9
Revises: a7c3f9e1d210
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f4a1c7e2b9"
down_revision: str | None = "a7c3f9e1d210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_identities",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=191), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_oauth_identities_users_user_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_identities")),
        sa.UniqueConstraint(
            "provider", "subject", name=op.f("uq_oauth_identities_provider")
        ),
    )
    op.create_index(
        op.f("ix_oauth_identities_tenant_id"),
        "oauth_identities",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_identities_provider"),
        "oauth_identities",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_identities_subject"),
        "oauth_identities",
        ["subject"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_identities_user_id"),
        "oauth_identities",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_oauth_identities_user_id"), table_name="oauth_identities"
    )
    op.drop_index(
        op.f("ix_oauth_identities_subject"), table_name="oauth_identities"
    )
    op.drop_index(
        op.f("ix_oauth_identities_provider"), table_name="oauth_identities"
    )
    op.drop_index(
        op.f("ix_oauth_identities_tenant_id"), table_name="oauth_identities"
    )
    op.drop_table("oauth_identities")
