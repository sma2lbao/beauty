"""run_artifacts 多件交付物列(去唯一约束 + 文件元数据 + 二进制)

背景:一个 run 过去只能有一件 Markdown 报告(唯一约束 uq_run_artifacts_run_id),
技能/工具产出的真实文件(截图、抓取到的 PDF 等)只能丢弃。本迁移:

- 去掉 run_id 唯一约束,允许一个 run 多件交付物;
- 加 filename/content_type/source/source_url/content_bytes:
  content 继续只放文本,二进制单独入 content_bytes;
- 加 workspace_artifact_id:与 workspace-service 产物镜像的幂等键。

source 用 server_default='report' 回填旧行(阶段 A 只有报告),SQLite 走
batch 重建表、Postgres 直接 ALTER,downgrade 无法恢复多件数据(会重建唯一约束)。

Revision ID: b6f1c8d92a47
Revises: d4f9a2c7e1b8
Create Date: 2026-10-21 23:58:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6f1c8d92a47"
down_revision: str | None = "d4f9a2c7e1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("run_artifacts") as batch:
        batch.drop_constraint("uq_run_artifacts_run_id", type_="unique")
        batch.add_column(sa.Column("filename", sa.String(length=255), nullable=True))
        batch.add_column(
            sa.Column("content_type", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "source",
                sa.String(length=32),
                nullable=False,
                server_default="report",
            )
        )
        batch.add_column(sa.Column("source_url", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("content_bytes", sa.LargeBinary(), nullable=True))
        batch.add_column(
            sa.Column("workspace_artifact_id", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    # 注意:多个交付物的 run 无法回退到"每 run 一件",downgrade 会因唯一约束失败;
    # 这是有损迁移,生产回退前需先清理多余的行。
    with op.batch_alter_table("run_artifacts") as batch:
        batch.drop_column("workspace_artifact_id")
        batch.drop_column("content_bytes")
        batch.drop_column("source_url")
        batch.drop_column("source")
        batch.drop_column("content_type")
        batch.drop_column("filename")
        batch.create_unique_constraint("uq_run_artifacts_run_id", ["run_id"])
