"""记忆生命周期与复核队列(P3-B/R4–R8)

- memories.valid_from / invalid_at / superseded_by:事实时效与替代链;
  写入新事实时旧条目只标失效并指向替代者,历史保留可追溯;
- memories.confidence:抽取置信度,低置信度先入复核队列;
- memories.injection_mode:retrieved(按相关性召回)/ always(常驻核心块);
- memory_reviews:低置信度候选的人工复核队列(通过才落成记忆)。

SQLite 不支持原地改列,统一走 batch_alter_table;存量行回填
valid_from = created_at,并给新列 server_default 保证旧数据可用。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6a1d4e8f2b7"
down_revision: str | None = "e3c9a1f7b5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memories") as batch_op:
        batch_op.add_column(
            sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("invalid_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("superseded_by", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "confidence",
                sa.Float(),
                nullable=False,
                server_default="1.0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "injection_mode",
                sa.String(length=16),
                nullable=False,
                server_default="retrieved",
            )
        )
        batch_op.create_index(
            "ix_memories_superseded_by", ["superseded_by"], unique=False
        )
    # 存量记忆视为自创建起有效;已有失效语义的行不受影响(新列刚加,全为 NULL)。
    op.execute(
        "UPDATE memories SET valid_from = created_at WHERE valid_from IS NULL"
    )
    op.create_table(
        "memory_reviews",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
        sa.Column(
            "scope", sa.String(length=16), nullable=False, server_default="account"
        ),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="note"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "confidence", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column("evidence_quote", sa.Text(), nullable=True),
        sa.Column(
            "source_type", sa.String(length=16), nullable=False, server_default="chat"
        ),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("resolved_memory_id", sa.String(length=32), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
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
    )
    op.create_index(
        "ix_memory_reviews_account_id", "memory_reviews", ["account_id"], unique=False
    )
    op.create_index(
        "ix_memory_reviews_workspace_id",
        "memory_reviews",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_memory_reviews_workspace_id", table_name="memory_reviews")
    op.drop_index("ix_memory_reviews_account_id", table_name="memory_reviews")
    op.drop_table("memory_reviews")
    with op.batch_alter_table("memories") as batch_op:
        batch_op.drop_index("ix_memories_superseded_by")
        batch_op.drop_column("injection_mode")
        batch_op.drop_column("confidence")
        batch_op.drop_column("superseded_by")
        batch_op.drop_column("invalid_at")
        batch_op.drop_column("valid_from")
