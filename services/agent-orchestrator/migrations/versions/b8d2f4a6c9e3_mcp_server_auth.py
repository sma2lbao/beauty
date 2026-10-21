"""mcp_server_auth: 远程 MCP 浏览器 OAuth 授权(P1)

用户在浏览器完成授权码流程后,服务端保存授权服务器、动态注册的 client 与
加密后的 access/refresh token,供工具调用前取用与过期刷新。

Revision ID: b8d2f4a6c9e3
Revises: c3a7e9d1b4f6
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2f4a6c9e3"
down_revision: str | None = "c3a7e9d1b4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_server_auth",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("server_id", sa.String(32), nullable=False),
        sa.Column("authorization_server", sa.String(500), nullable=True),
        sa.Column("token_endpoint", sa.String(500), nullable=True),
        sa.Column("client_id", sa.String(300), nullable=True),
        sa.Column("encrypted_client_secret", sa.Text(), nullable=True),
        sa.Column("redirect_uri", sa.String(500), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(32), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_server_auth")),
        sa.UniqueConstraint(
            "tenant_id", "server_id", name=op.f("uq_mcp_server_auth_tenant_id")
        ),
    )
    op.create_index(
        op.f("ix_mcp_server_auth_tenant_id"),
        "mcp_server_auth",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mcp_server_auth_tenant_id"), table_name="mcp_server_auth")
    op.drop_table("mcp_server_auth")
