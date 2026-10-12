"""runs 状态原因列正名 status_reason

背景:`blocked_reason` 同时承载 blocked(拒绝计划/验收未通过)与 failed(异常收敛)
的原因,名字只覆盖了一半语义。改为 `status_reason`——含义由 status 决定。

兼容性:纯重命名(SQLite 3.25+ / Postgres 支持 RENAME COLUMN),**数据原地保留**;
downgrade 反向改名,可安全回滚。API 字段随之改名(前端同一提交内已更新)。

Revision ID: e6f1b4003314
Revises: 7b57cfef4c35
Create Date: 2026-10-13 01:07:38.603806

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f1b4003314"
down_revision: Union[str, None] = "7b57cfef4c35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("runs", "blocked_reason", new_column_name="status_reason")


def downgrade() -> None:
    op.alter_column("runs", "status_reason", new_column_name="blocked_reason")
