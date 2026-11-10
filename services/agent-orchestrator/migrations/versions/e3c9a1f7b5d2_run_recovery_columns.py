"""run 超时/优先级与恢复语义所需的列

P1-C(docs/design/recovery.md §3):

- runs.timeout_seconds:单 run 显式超时(NULL = 用全局 settings 默认);
  runs.priority:调度优先级(默认 0);runs.started_at:首次进入 planning/running
  的时刻,超时基准取 coalesce(started_at, created_at),只写一次;
- jobs.priority:入队时从 run 复制,领取顺序改为 priority DESC, run_at,
  created_at,因此索引从 (status, run_at) 换成 (status, priority, run_at)。

SQLite 不支持 ALTER 约束/索引原地改,统一走 batch_alter_table 重建表;
downgrade 反向删列并恢复旧索引。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3c9a1f7b5d2"
down_revision: str | None = "b7f3d1c8e4a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(sa.Column("timeout_seconds", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.drop_index("ix_jobs_status_run_at", table_name="jobs")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
        )
    op.create_index(
        "ix_jobs_status_priority_run_at",
        "jobs",
        ["status", "priority", "run_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_status_priority_run_at", table_name="jobs")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("priority")
    op.create_index("ix_jobs_status_run_at", "jobs", ["status", "run_at"], unique=False)
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("started_at")
        batch_op.drop_column("priority")
        batch_op.drop_column("timeout_seconds")
