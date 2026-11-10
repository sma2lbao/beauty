"""账号级应用 hooks(webhook)

P1-B:应用级 hooks(design/hooks.md §3/§5)。新表 account_hooks:

- 一行 = 一个事件 + 一个 matcher(工具名精确/glob)+ 一个 HTTPS target;
- **不加唯一约束**:同一事件允许多条,按 created_at, id 升序串行执行,
  首个 deny/skip 短路(§4.2);
- 索引 (account_id, event):列表与执行侧快照都按账号 + 事件过滤;
- secret 是 HMAC 签名密钥,DB 明文存储是已记录残余(§9)。

downgrade 直接删表(表内只有账号配置,可由 API 重建)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f3d1c8e4a2"
down_revision: str | None = "e2b7c4a9d1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_hooks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("matcher", sa.String(length=200), nullable=False, server_default="*"),
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default="webhook"
        ),
        sa.Column("target", sa.String(length=1024), nullable=False),
        sa.Column("secret", sa.String(length=128), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_hooks_account_id",
        "account_hooks",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_account_hooks_account_event",
        "account_hooks",
        ["account_id", "event"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_account_hooks_account_event", table_name="account_hooks")
    op.drop_index("ix_account_hooks_account_id", table_name="account_hooks")
    op.drop_table("account_hooks")
