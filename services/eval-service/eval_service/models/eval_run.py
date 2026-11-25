"""评测运行/套件/用例/基线表。

报告超过阈值时把原始 JSON 转存对象存储，DB 只留摘要与对象键；
suite/case 的冗余列只服务列表与分页，不替代 report_json 这份原始记录。
"""

from __future__ import annotations

from datetime import UTC, datetime

from database.base import Base, TimestampMixin, UUIDMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column


class EvalRun(UUIDMixin, TimestampMixin, Base):
    """一次平台评测运行（跨套件）。"""

    __tablename__ = "eval_runs"
    __table_args__ = (
        UniqueConstraint("external_id", name="uq_eval_runs_external_id"),
        Index("ix_eval_runs_created_at", "created_at"),
    )

    # 报告文件名/CI run id 等外部稳定键；为空时不做幂等。
    external_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    git_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    branch: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    pr: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="scripted")
    environment: Mapped[str] = mapped_column(
        String(32), nullable=False, default="local"
    )
    report_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 原始报告（含所有用例的 fields/events/tool_calls）。
    # 大报告转存对象存储后，report_json 只保留不含用例明细的摘要。
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
    report_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True, default=None
    )
    report_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    totals_json: Mapped[str] = mapped_column(Text, nullable=False)
    suites_json: Mapped[str] = mapped_column(Text, nullable=False)
    failed_cases_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    dataset_versions_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )
    drifts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    drift_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class EvalSuiteRun(UUIDMixin, TimestampMixin, Base):
    """运行内的一个套件摘要。"""

    __tablename__ = "eval_suite_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_eval_suite_runs_run_name"),
        Index("ix_eval_suite_runs_run", "run_id"),
    )

    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dataset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    totals_json: Mapped[str] = mapped_column(Text, nullable=False)
    drifts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    drift_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EvalCaseRun(UUIDMixin, TimestampMixin, Base):
    """运行内的一个用例结果（列表分页与下钻用）。"""

    __tablename__ = "eval_case_runs"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "suite", "case_id", name="uq_eval_case_runs_run_case"
        ),
        Index("ix_eval_case_runs_page", "run_id", "suite", "case_id"),
        Index("ix_eval_case_runs_status", "run_id", "suite", "ok"),
    )

    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    suite: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[str] = mapped_column(String(256), nullable=False)
    group: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    fields_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    events_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tool_calls_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    graders_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    failures_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    baseline_diff_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class EvalReportDownload(UUIDMixin, Base):
    """报告下载审计:append-only，记录成功与失败尝试，不随保留期删除。"""

    __tablename__ = "eval_report_downloads"
    __table_args__ = (
        Index("ix_eval_report_downloads_created_at", "created_at"),
        Index("ix_eval_report_downloads_run", "run_id"),
    )

    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="anonymous"
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    report_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    format: Mapped[str] = mapped_column(String(16), nullable=False, default="json")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class EvalBaseline(UUIDMixin, TimestampMixin, Base):
    """已批准基线:同一时刻每个套件只有一条（按 suite 唯一）。"""

    __tablename__ = "eval_baselines"
    __table_args__ = (
        UniqueConstraint("suite", name="uq_eval_baselines_suite"),
        Index("ix_eval_baselines_suite", "suite"),
    )

    suite: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluator_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1"
    )
    git_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
