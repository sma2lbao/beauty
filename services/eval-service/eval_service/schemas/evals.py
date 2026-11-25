"""评测 HTTP schema:上报信封、索引、分页与基线。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

REPORT_VERSION = 1


class EvalRunUpload(BaseModel):
    """POST /v1/runs 的上报信封。

    report 原样保存；baselines 可选（suite -> 基线 payload），有基线时服务
    在入库时计算漂移；没有时回落到该套件已批准基线。
    """

    model_config = ConfigDict(extra="forbid")

    external_id: str | None = Field(default=None, max_length=128)
    trigger: str = Field(default="local", max_length=32)
    git_sha: str = Field(default="", max_length=64)
    branch: str = Field(default="", max_length=128)
    pr: int | None = Field(default=None, ge=1)
    model: str = Field(default="scripted", max_length=128)
    environment: str = Field(default="local", max_length=32)
    report: dict[str, Any]
    baselines: dict[str, dict[str, Any]] = Field(default_factory=dict)


class EvalTotalsRead(BaseModel):
    cases: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    score: float = 0.0
    tokens: int = 0
    duration_ms: float = 0.0


class EvalSuiteSummaryRead(BaseModel):
    name: str
    dataset: str | None = None
    dataset_version: int | None = None
    totals: EvalTotalsRead
    drift_count: int = 0


class EvalFailedCaseRead(BaseModel):
    suite: str
    case_id: str
    failures: list[str] = Field(default_factory=list)


class EvalRunRead(BaseModel):
    """运行索引项；字段与前端 EvalRunMeta 对齐。"""

    id: str
    created_at: datetime
    trigger: str
    git_sha: str
    branch: str
    pr: int | None = None
    model: str
    environment: str
    report_version: int
    totals: EvalTotalsRead
    drift_count: int = 0
    suites: list[EvalSuiteSummaryRead] = Field(default_factory=list)
    failed_cases: list[EvalFailedCaseRead] = Field(default_factory=list)
    dataset_versions: dict[str, int | None] = Field(default_factory=dict)


class EvalIndexRead(BaseModel):
    version: int = 1
    generated_at: datetime
    runs: list[EvalRunRead] = Field(default_factory=list)


class EvalCaseRead(BaseModel):
    """用例分页项；字段与 evalkit CaseRun.to_dict() 对齐。"""

    id: str
    group: str = ""
    tags: list[str] = Field(default_factory=list)
    ok: bool
    score: float = 0.0
    duration_ms: float = 0.0
    tokens: int = 0
    output: str = ""
    error: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    events: list[Any] = Field(default_factory=list)
    tool_calls: list[Any] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    graders: list[Any] = Field(default_factory=list)
    failures: list[Any] = Field(default_factory=list)
    baseline_diff: list[Any] = Field(default_factory=list)


class EvalCasePageRead(BaseModel):
    items: list[EvalCaseRead]
    total: int
    limit: int
    offset: int


class EvalDatasetCaseRead(BaseModel):
    """数据集用例只读视图；input/graders 原样返回，供页面解释评分口径。"""

    id: str
    group: str = "default"
    tags: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    graders: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""


class EvalDatasetSummaryRead(BaseModel):
    """数据集清单项；坏数据集也保留在清单里，valid=false + error 说明原因。"""

    name: str
    version: int = 0
    description: str = ""
    owner: str = ""
    adapter: str = ""
    tags: list[str] = Field(default_factory=list)
    case_count: int = 0
    groups: list[str] = Field(default_factory=list)
    valid: bool = True
    error: str | None = None


class EvalDatasetRead(EvalDatasetSummaryRead):
    cases: list[EvalDatasetCaseRead] = Field(default_factory=list)


class EvalDatasetListRead(BaseModel):
    items: list[EvalDatasetSummaryRead] = Field(default_factory=list)


class EvalCompareTotalsRead(BaseModel):
    """两次运行的用例级差异汇总;通过率/tokens 差值均按 candidate - base。"""

    regressed: int = 0
    fixed: int = 0
    added: int = 0
    removed: int = 0
    unchanged: int = 0
    base_pass_rate: float = 0.0
    candidate_pass_rate: float = 0.0
    pass_rate_delta: float = 0.0
    tokens_delta: int = 0
    duration_delta_ms: float = 0.0


class EvalCompareSuiteRead(BaseModel):
    name: str
    base_totals: EvalTotalsRead | None = None
    candidate_totals: EvalTotalsRead | None = None
    regressed: int = 0
    fixed: int = 0
    added: int = 0
    removed: int = 0
    unchanged: int = 0


class EvalCompareCaseRead(BaseModel):
    """单条用例差异;status=regressed/fixed/added/removed/unchanged。"""

    suite: str
    case_id: str
    status: str
    base_ok: bool | None = None
    candidate_ok: bool | None = None
    base_score: float | None = None
    candidate_score: float | None = None
    score_delta: float = 0.0
    tokens_delta: int = 0
    duration_delta_ms: float = 0.0
    base_error: str | None = None
    candidate_error: str | None = None
    failures: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class EvalCompareRead(BaseModel):
    base: EvalRunRead
    candidate: EvalRunRead
    totals: EvalCompareTotalsRead
    suites: list[EvalCompareSuiteRead] = Field(default_factory=list)
    cases: list[EvalCompareCaseRead] = Field(default_factory=list)


class EvalBaselineRead(BaseModel):
    suite: str
    dataset_version: int | None = None
    evaluator_version: str = "1"
    git_sha: str = ""
    approved_by: str = ""
    approved_at: datetime
    case_count: int = 0
    passed: int = 0


class EvalBaselineListRead(BaseModel):
    items: list[EvalBaselineRead] = Field(default_factory=list)


class EvalBaselineApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    dataset_version: int | None = None
    evaluator_version: str = Field(default="1", max_length=32)
    git_sha: str = Field(default="", max_length=64)
