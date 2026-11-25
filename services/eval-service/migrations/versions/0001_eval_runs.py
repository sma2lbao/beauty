"""create eval tables

Revision ID: 0001_eval_runs
Revises:
Create Date: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_eval_runs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("git_sha", sa.String(length=64), nullable=False),
        sa.Column("branch", sa.String(length=128), nullable=False),
        sa.Column("pr", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("report_version", sa.Integer(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.Column("totals_json", sa.Text(), nullable=False),
        sa.Column("suites_json", sa.Text(), nullable=False),
        sa.Column("failed_cases_json", sa.Text(), nullable=False),
        sa.Column("dataset_versions_json", sa.Text(), nullable=False),
        sa.Column("drifts_json", sa.Text(), nullable=False),
        sa.Column("drift_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
        sa.UniqueConstraint("external_id", name="uq_eval_runs_external_id"),
    )
    op.create_index("ix_eval_runs_created_at", "eval_runs", ["created_at"])
    op.create_table(
        "eval_suite_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("dataset", sa.String(length=256), nullable=True),
        sa.Column("dataset_version", sa.Integer(), nullable=True),
        sa.Column("totals_json", sa.Text(), nullable=False),
        sa.Column("drifts_json", sa.Text(), nullable=False),
        sa.Column("drift_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_suite_runs")),
        sa.UniqueConstraint("run_id", "name", name="uq_eval_suite_runs_run_name"),
    )
    op.create_index("ix_eval_suite_runs_run", "eval_suite_runs", ["run_id"])
    op.create_table(
        "eval_case_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("suite", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=256), nullable=False),
        sa.Column("group", sa.String(length=128), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("fields_json", sa.Text(), nullable=False),
        sa.Column("events_json", sa.Text(), nullable=False),
        sa.Column("tool_calls_json", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("graders_json", sa.Text(), nullable=False),
        sa.Column("failures_json", sa.Text(), nullable=False),
        sa.Column("baseline_diff_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_case_runs")),
        sa.UniqueConstraint(
            "run_id", "suite", "case_id", name="uq_eval_case_runs_run_case"
        ),
    )
    op.create_index(
        "ix_eval_case_runs_page",
        "eval_case_runs",
        ["run_id", "suite", "case_id"],
    )
    op.create_index(
        "ix_eval_case_runs_status",
        "eval_case_runs",
        ["run_id", "suite", "ok"],
    )
    op.create_table(
        "eval_baselines",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("suite", sa.String(length=128), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=True),
        sa.Column("evaluator_version", sa.String(length=32), nullable=False),
        sa.Column("git_sha", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_baselines")),
        sa.UniqueConstraint("suite", name="uq_eval_baselines_suite"),
    )
    op.create_index("ix_eval_baselines_suite", "eval_baselines", ["suite"])


def downgrade() -> None:
    op.drop_index("ix_eval_baselines_suite", table_name="eval_baselines")
    op.drop_table("eval_baselines")
    op.drop_index("ix_eval_case_runs_status", table_name="eval_case_runs")
    op.drop_index("ix_eval_case_runs_page", table_name="eval_case_runs")
    op.drop_table("eval_case_runs")
    op.drop_index("ix_eval_suite_runs_run", table_name="eval_suite_runs")
    op.drop_table("eval_suite_runs")
    op.drop_index("ix_eval_runs_created_at", table_name="eval_runs")
    op.drop_table("eval_runs")
