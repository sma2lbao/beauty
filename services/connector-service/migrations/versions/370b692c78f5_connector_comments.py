"""连接器平台:补齐表/列注释(与模型对齐)

`8f1c2a5d7b90` 用 `op.add_column` 增补 connections 生命周期字段时未带
`comment=`,表注释也停留在改名前的「租户」措辞,导致 `alembic check` 在真实
Postgres 上一直报注释漂移。本迁移只改注释,不动结构;SQLite 等不支持注释的
方言直接跳过。

Revision ID: 370b692c78f5
Revises: 4c70941e7a37
Create Date: 2026-11-12 21:52:45.894986

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "370b692c78f5"
down_revision: str | None = "4c70941e7a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (表名, 新注释, 改名前的旧注释)
_TABLE_COMMENTS = (
    ("account_apps", "按账号覆盖 provider 应用凭证", "按租户覆盖 provider 应用凭证"),
    (
        "connections",
        "同账号同提供商同外部账号只允许一条连接",
        "同租户同提供商同账号只允许一条连接",
    ),
)


def upgrade() -> None:
    if not op.get_bind().dialect.supports_comments:
        return
    for table_name, comment, old_comment in _TABLE_COMMENTS:
        op.create_table_comment(
            table_name,
            comment,
            existing_comment=old_comment,
            schema=None,
        )
    op.alter_column(
        "connections",
        "owner_subject",
        existing_type=sa.VARCHAR(length=64),
        comment="授权用户 subject;无用户身份时为空",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "auth_kind",
        existing_type=sa.VARCHAR(length=16),
        comment="授权主体: user/token",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "provider_account_id",
        existing_type=sa.VARCHAR(length=128),
        comment="provider 账号 id",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "external_workspace",
        existing_type=sa.VARCHAR(length=128),
        comment="provider 工作区/账号标识",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "scopes",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        comment="实际授予的 scope 列表",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "metadata",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        comment="provider 附加信息(workspace/user/app 等)",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "enabled",
        existing_type=sa.BOOLEAN(),
        comment="关闭启用 != 解绑;默认 true",
        existing_nullable=False,
        existing_server_default=sa.text("true"),
    )
    op.alter_column(
        "connections",
        "expires_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        comment="access token 过期时间",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "refresh_expires_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        comment="refresh token 过期时间(由 provider 决定)",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "last_refreshed_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        comment="最近一次成功刷新时间",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "last_error_code",
        existing_type=sa.VARCHAR(length=64),
        comment="最近一次失败码(前端展示/判定重连)",
        existing_nullable=True,
    )


def downgrade() -> None:
    if not op.get_bind().dialect.supports_comments:
        return
    op.alter_column(
        "connections",
        "last_error_code",
        existing_type=sa.VARCHAR(length=64),
        comment=None,
        existing_comment="最近一次失败码(前端展示/判定重连)",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "last_refreshed_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        comment=None,
        existing_comment="最近一次成功刷新时间",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "refresh_expires_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        comment=None,
        existing_comment="refresh token 过期时间(由 provider 决定)",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "expires_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        comment=None,
        existing_comment="access token 过期时间",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "enabled",
        existing_type=sa.BOOLEAN(),
        comment=None,
        existing_comment="关闭启用 != 解绑;默认 true",
        existing_nullable=False,
        existing_server_default=sa.text("true"),
    )
    op.alter_column(
        "connections",
        "metadata",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        comment=None,
        existing_comment="provider 附加信息(workspace/user/app 等)",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "scopes",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        comment=None,
        existing_comment="实际授予的 scope 列表",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "external_workspace",
        existing_type=sa.VARCHAR(length=128),
        comment=None,
        existing_comment="provider 工作区/账号标识",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "provider_account_id",
        existing_type=sa.VARCHAR(length=128),
        comment=None,
        existing_comment="provider 账号 id",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "auth_kind",
        existing_type=sa.VARCHAR(length=16),
        comment=None,
        existing_comment="授权主体: user/token",
        existing_nullable=True,
    )
    op.alter_column(
        "connections",
        "owner_subject",
        existing_type=sa.VARCHAR(length=64),
        comment=None,
        existing_comment="授权用户 subject;无用户身份时为空",
        existing_nullable=True,
    )
    for table_name, comment, old_comment in reversed(_TABLE_COMMENTS):
        op.create_table_comment(
            table_name,
            old_comment,
            existing_comment=comment,
            schema=None,
        )
