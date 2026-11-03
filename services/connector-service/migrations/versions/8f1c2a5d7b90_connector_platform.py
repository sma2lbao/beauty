"""连接器平台:连接扩展列 + 授权会话 + 租户应用凭证

- connections 增补连接生命周期字段(owner/auth_kind/外部标识/scopes/metadata/
  enabled/过期与刷新痕迹/撤销留痕);唯一约束不变。
- oauth_sessions:一次性授权会话,state 只落随机 nonce。
- tenant_apps:按租户覆盖 provider 应用凭证(v1 只有配置/脚本写入)。

Revision ID: 8f1c2a5d7b90
Revises: b2e4c7a91d33
Create Date: 2026-11-03 23:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f1c2a5d7b90"
down_revision: str | None = "b2e4c7a91d33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_CONNECTION_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("owner_subject", sa.String(length=64), nullable=True),
    sa.Column("auth_kind", sa.String(length=16), nullable=True),
    sa.Column("provider_account_id", sa.String(length=128), nullable=True),
    sa.Column("external_workspace", sa.String(length=128), nullable=True),
    sa.Column("scopes", sa.JSON(), nullable=True),
    sa.Column("metadata", sa.JSON(), nullable=True),
    sa.Column(
        "enabled", sa.Boolean(), nullable=False, server_default=sa.true()
    ),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_error_code", sa.String(length=64), nullable=True),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("revoked_by", sa.String(length=64), nullable=True),
)

_TIMESTAMP_COLUMNS = (
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
    sa.Column("tenant_id", sa.String(length=32), nullable=False),
)


def upgrade() -> None:
    for column in _NEW_CONNECTION_COLUMNS:
        op.add_column("connections", column)
    if op.get_bind().dialect.supports_comments:
        op.alter_column(
            "connections",
            "provider",
            comment="provider 名(如 feishu)",
        )
        op.alter_column(
            "connections",
            "credentials",
            comment="提供商凭证;读端永不返回;Fernet 信封加密存储",
        )
        op.alter_column(
            "connections",
            "account",
            comment="稳定账号标识(飞书 open_id)",
        )

    op.create_table(
        "oauth_sessions",
        sa.Column("owner_subject", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state_nonce", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=True),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            comment="pending/completed/failed/expired",
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("connection_id", sa.String(length=32), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        *_TIMESTAMP_COLUMNS,
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_sessions")),
        comment="一次性 OAuth 授权会话(nonce 换身份)",
    )
    op.create_index(
        op.f("ix_oauth_sessions_state_nonce"),
        "oauth_sessions",
        ["state_nonce"],
        unique=True,
    )
    op.create_index(
        op.f("ix_oauth_sessions_status"),
        "oauth_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_sessions_tenant_id"),
        "oauth_sessions",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "tenant_apps",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("app_id", sa.String(length=128), nullable=False),
        sa.Column("app_secret_encrypted", sa.JSON(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        *_TIMESTAMP_COLUMNS,
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_apps")),
        sa.UniqueConstraint("tenant_id", "provider", name=op.f("uq_tenant_apps_tenant_id")),
        comment="按租户覆盖 provider 应用凭证",
    )
    op.create_index(
        op.f("ix_tenant_apps_tenant_id"),
        "tenant_apps",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tenant_apps_tenant_id"), table_name="tenant_apps")
    op.drop_table("tenant_apps")
    op.drop_index(op.f("ix_oauth_sessions_tenant_id"), table_name="oauth_sessions")
    op.drop_index(op.f("ix_oauth_sessions_status"), table_name="oauth_sessions")
    op.drop_index(op.f("ix_oauth_sessions_state_nonce"), table_name="oauth_sessions")
    op.drop_table("oauth_sessions")
    for column in reversed(_NEW_CONNECTION_COLUMNS):
        op.drop_column("connections", column.name)
