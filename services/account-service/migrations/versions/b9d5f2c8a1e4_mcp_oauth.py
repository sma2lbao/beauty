"""oauth_clients / codes / tokens:MCP OAuth 2.1(M3-04)

Revision ID: b9d5f2c8a1e4
Revises: a8c4e1f7b3d9
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9d5f2c8a1e4"
down_revision: str | None = "a8c4e1f7b3d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("client_name", sa.String(length=120), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_clients")),
    )
    op.create_index(
        op.f("ix_oauth_clients_client_id"),
        "oauth_clients",
        ["client_id"],
        unique=True,
    )
    op.create_table(
        "oauth_authorization_codes",
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("code_challenge", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_authorization_codes")),
    )
    op.create_index(
        op.f("ix_oauth_authorization_codes_tenant_id"),
        "oauth_authorization_codes",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_authorization_codes_code_hash"),
        "oauth_authorization_codes",
        ["code_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_oauth_authorization_codes_client_id"),
        "oauth_authorization_codes",
        ["client_id"],
        unique=False,
    )
    op.create_table(
        "oauth_tokens",
        sa.Column("access_hash", sa.String(length=64), nullable=False),
        sa.Column("refresh_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_tokens")),
    )
    op.create_index(
        op.f("ix_oauth_tokens_tenant_id"),
        "oauth_tokens",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_tokens_access_hash"),
        "oauth_tokens",
        ["access_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_oauth_tokens_refresh_hash"),
        "oauth_tokens",
        ["refresh_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_oauth_tokens_client_id"), "oauth_tokens", ["client_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_oauth_tokens_client_id"), table_name="oauth_tokens")
    op.drop_index(op.f("ix_oauth_tokens_refresh_hash"), table_name="oauth_tokens")
    op.drop_index(op.f("ix_oauth_tokens_access_hash"), table_name="oauth_tokens")
    op.drop_index(op.f("ix_oauth_tokens_tenant_id"), table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
    op.drop_index(
        op.f("ix_oauth_authorization_codes_client_id"),
        table_name="oauth_authorization_codes",
    )
    op.drop_index(
        op.f("ix_oauth_authorization_codes_code_hash"),
        table_name="oauth_authorization_codes",
    )
    op.drop_index(
        op.f("ix_oauth_authorization_codes_tenant_id"),
        table_name="oauth_authorization_codes",
    )
    op.drop_table("oauth_authorization_codes")
    op.drop_index(
        op.f("ix_oauth_clients_client_id"), table_name="oauth_clients"
    )
    op.drop_table("oauth_clients")
