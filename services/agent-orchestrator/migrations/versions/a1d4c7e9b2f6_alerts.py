"""运维告警事件表(docs/design/alerting.md)

`alerts` 是 append-only 事件流,一行 = 某规则在某时刻的状态切换(firing/resolved)。
不带 account_id:告警描述的是部署健康度,不是账号私有数据。

唯一约束 `(code, status, bucket)` 让多副本同时评估时同一去重桶只落一条;
`(code, created_at)` 索引服务"每个 code 取最新一行"的当前状态查询。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1d4c7e9b2f6"
down_revision: str | None = "c6a1d4e8f2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column(
            "severity", sa.String(length=16), nullable=False, server_default="warning"
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("bucket", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "code", "status", "bucket", name="uq_alerts_code_status_bucket"
        ),
    )
    op.create_index(
        "ix_alerts_code_created_at", "alerts", ["code", "created_at"], unique=False
    )
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_alerts_created_at", table_name="alerts")
    op.drop_index("ix_alerts_code_created_at", table_name="alerts")
    op.drop_table("alerts")
