"""沙箱实例表

Revision ID: c4d8e6f2a9b1
Revises: 895bb1452ed8
Create Date: 2026-10-20 22:30:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d8e6f2a9b1"
down_revision: Union[str, None] = "895bb1452ed8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sandboxes",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_name", sa.String(length=128), nullable=False),
        sa.Column("image", sa.String(length=120), nullable=False),
        sa.Column("cpus", sa.Float(), nullable=False),
        sa.Column("memory_mb", sa.Integer(), nullable=False),
        sa.Column("network", sa.String(length=16), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sandboxes")),
    )
    op.create_index(
        op.f("ix_sandboxes_expires_at"), "sandboxes", ["expires_at"], unique=False
    )
    op.create_index(op.f("ix_sandboxes_name"), "sandboxes", ["name"], unique=True)
    op.create_index(op.f("ix_sandboxes_status"), "sandboxes", ["status"], unique=False)
    op.create_index(
        op.f("ix_sandboxes_tenant_id"), "sandboxes", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sandboxes_tenant_id"), table_name="sandboxes")
    op.drop_index(op.f("ix_sandboxes_status"), table_name="sandboxes")
    op.drop_index(op.f("ix_sandboxes_name"), table_name="sandboxes")
    op.drop_index(op.f("ix_sandboxes_expires_at"), table_name="sandboxes")
    op.drop_table("sandboxes")
