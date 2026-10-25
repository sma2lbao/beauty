"""agent-orchestrator 业务指标(W4-R-40:从 `common.metrics` 迁出)。

指标名与标签与迁移前完全一致(docs/observability.md §9):

- `run_outcomes_total{service,status}`:run 终态计数;
- `job_failures_total{service,kind}`:后台任务失败计数;
- `llm_upstream_errors_total{service,provider,code}`:模型网关上游错误。

`service` 标签统一走 `common.metrics.service_label`
(显式值 > 日志上下文 > 装配时登记的默认值),后台任务也能打对标签。
"""

from __future__ import annotations

from common.metrics import service_label
from prometheus_client import Counter

run_outcomes_total = Counter(
    "run_outcomes_total",
    "任务终态计数",
    ("service", "status"),
)
job_failures_total = Counter(
    "job_failures_total",
    "job 失败计数",
    ("service", "kind"),
)
llm_upstream_errors_total = Counter(
    "llm_upstream_errors_total",
    "模型网关上游错误计数",
    ("service", "provider", "code"),
)


def observe_run_outcome(*, status: str, service: str | None = None) -> None:
    """记录一次 run 终态。"""
    run_outcomes_total.labels(service=service_label(service), status=status).inc()


def observe_job_failure(*, kind: str, service: str | None = None) -> None:
    """记录一次后台任务失败。"""
    job_failures_total.labels(service=service_label(service), kind=kind).inc()


def observe_llm_error(*, provider: str, code: str, service: str | None = None) -> None:
    """记录一次模型调用失败(provider/code 空值归一为 unknown)。"""
    llm_upstream_errors_total.labels(
        service=service_label(service),
        provider=provider or "unknown",
        code=code or "unknown",
    ).inc()
