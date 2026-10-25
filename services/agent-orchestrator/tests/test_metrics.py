"""业务指标(W4-R-40):域计数从 common.metrics 迁到 agent_orchestrator.metrics。

覆盖三件事:

1. 三个域 Counter 在默认注册表里按老名字注册(指标名/标签不变);
2. `observe_*` 真正累计(不是空实现);
3. `service` 标签沿用 common 的解析顺序:显式 > 日志上下文 > 装配默认。

原 common 侧的同名用例随指标归属一并搬到这里,覆盖不减。
"""

from __future__ import annotations

from typing import Any

import structlog
from common.logging import clear_request_context
from common.metrics import configure, metrics_payload

from agent_orchestrator.metrics import (
    job_failures_total,
    llm_upstream_errors_total,
    observe_job_failure,
    observe_llm_error,
    observe_run_outcome,
    run_outcomes_total,
)


def _value(counter: Any, **labels: str) -> float:
    return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]


def test_domain_counters_are_registered() -> None:
    """三个域指标的注册名与标签保持迁移前口径(prometheus 文本可查)。"""
    payload = metrics_payload()
    assert b"# TYPE run_outcomes_total counter" in payload
    assert b"# TYPE job_failures_total counter" in payload
    assert b"# TYPE llm_upstream_errors_total counter" in payload


def test_domain_counters_increment() -> None:
    observe_run_outcome(status="completed", service="svc-domain")
    observe_job_failure(kind="plan", service="svc-domain")
    observe_llm_error(
        provider="openai", code="llm.upstream_error", service="svc-domain"
    )
    assert _value(run_outcomes_total, service="svc-domain", status="completed") == 1
    assert _value(job_failures_total, service="svc-domain", kind="plan") == 1
    assert (
        _value(
            llm_upstream_errors_total,
            service="svc-domain",
            provider="openai",
            code="llm.upstream_error",
        )
        == 1
    )


def test_service_name_defaults_to_log_context() -> None:
    """业务埋点不传 service 时,用日志上下文里启动时绑定的服务名。"""
    structlog.contextvars.bind_contextvars(service="svc-ctx")
    try:
        observe_job_failure(kind="approve")
    finally:
        clear_request_context()
    assert _value(job_failures_total, service="svc-ctx", kind="approve") == 1


def test_service_name_falls_back_to_configured_default() -> None:
    """后台任务没有请求上下文时,用装配时登记的默认服务名。"""
    structlog.contextvars.clear_contextvars()
    configure(service="svc-configured")
    try:
        observe_job_failure(kind="resume")
        assert _value(job_failures_total, service="svc-configured", kind="resume") == 1
    finally:
        configure(service="unknown")
