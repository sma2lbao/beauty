"""usage_events:投递幂等键(P0-3)

用量上报改走 orchestrator outbox(至少一次投递),account 侧按
`event_id = <outbox 信封 id>:<样本序号>` 去重:同一账号内唯一,
重投不会重复计费;旧直连调用不传 event_id(NULL 不参与唯一约束)。

Revision ID: a9d4f2c8b6e1
Revises: e5b2d9c4a7f1
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d4f2c8b6e1"
down_revision: str | None = "e5b2d9c4a7f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite 下加唯一约束要重建表;与 add_column 分开两个 batch,
    # 否则约束会被静默丢弃(同 60b129ed3713 的注释)。
    with op.batch_alter_table("usage_events", recreate="always") as batch:
        batch.add_column(sa.Column("event_id", sa.String(length=64), nullable=True))
    with op.batch_alter_table("usage_events", recreate="always") as batch:
        batch.create_unique_constraint(
            "uq_usage_events_account_id", ["account_id", "event_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("usage_events", recreate="always") as batch:
        batch.drop_constraint("uq_usage_events_account_id", type_="unique")
    with op.batch_alter_table("usage_events", recreate="always") as batch:
        batch.drop_column("event_id")
