"""agent-orchestrator 业务指标(W4-R-40:从 `common.metrics` 迁出)。

指标名与标签与迁移前完全一致(docs/observability.md §9):

- `run_outcomes_total{service,status}`:run 终态计数;
- `job_failures_total{service,kind}`:后台任务失败计数;
- `llm_upstream_errors_total{service,provider,code}`:模型网关上游错误;
- `memory_sweeps_total{service,outcome}` / `memory_sweep_items_total{service,action}` /
  `memory_sweep_account_failures_total{service}`:记忆清扫轮次、处理量与单账号失败。

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
memory_sweeps_total = Counter(
    "memory_sweeps_total",
    "记忆清扫轮次计数",
    ("service", "outcome"),
)
memory_sweep_items_total = Counter(
    "memory_sweep_items_total",
    "记忆清扫处理条目计数",
    ("service", "action"),
)
memory_sweep_account_failures_total = Counter(
    "memory_sweep_account_failures_total",
    "记忆清扫单账号失败计数",
    ("service",),
)
# 告警(docs/design/alerting.md):只记状态切换的次数,当前是否 firing 看
# `/internal/alerts`——指标不适合表达"当前状态"。
alerts_fired_total = Counter(
    "alerts_fired_total",
    "告警进入 firing 的次数",
    ("service", "code", "severity"),
)
alerts_resolved_total = Counter(
    "alerts_resolved_total",
    "告警恢复次数",
    ("service", "code"),
)


def observe_run_outcome(*, status: str, service: str | None = None) -> None:
    """记录一次 run 终态。"""
    run_outcomes_total.labels(service=service_label(service), status=status).inc()


def observe_job_failure(*, kind: str, service: str | None = None) -> None:
    """记录一次后台任务失败。"""
    job_failures_total.labels(service=service_label(service), kind=kind).inc()


def observe_memory_sweep(
    *,
    outcome: str,
    purged: int = 0,
    consolidated: int = 0,
    failed_accounts: int = 0,
    service: str | None = None,
) -> None:
    """记录一轮记忆清扫的结局与处理量(outcome=completed/skipped/failed)。"""
    label = service_label(service)
    memory_sweeps_total.labels(service=label, outcome=outcome).inc()
    if purged:
        memory_sweep_items_total.labels(service=label, action="purged").inc(purged)
    if consolidated:
        memory_sweep_items_total.labels(service=label, action="consolidated").inc(
            consolidated
        )
    if failed_accounts:
        memory_sweep_account_failures_total.labels(service=label).inc(failed_accounts)


def observe_alert_fired(
    *, code: str, severity: str, service: str | None = None
) -> None:
    """记录一次告警进入 firing(同一规则持续触发只在上报时计数,见 sweep 去重)。"""
    alerts_fired_total.labels(
        service=service_label(service), code=code, severity=severity
    ).inc()


def observe_alert_resolved(*, code: str, service: str | None = None) -> None:
    """记录一次告警恢复。"""
    alerts_resolved_total.labels(service=service_label(service), code=code).inc()


def observe_llm_error(*, provider: str, code: str, service: str | None = None) -> None:
    """记录一次模型调用失败(provider/code 空值归一为 unknown)。"""
    llm_upstream_errors_total.labels(
        service=service_label(service),
        provider=provider or "unknown",
        code=code or "unknown",
    ).inc()
