"""业务指标(W4-R-40):域计数从 common.metrics 迁到 llm_gateway.metrics。

token/耗时/错误三个指标的注册名与标签保持迁移前口径,并做一次真实累计。
"""

from __future__ import annotations

from typing import Any

from common.metrics import metrics_payload

from llm_gateway.metrics import (
    llm_request_duration_seconds,
    llm_tokens_total,
    llm_upstream_errors_total,
    observe_llm_error,
    observe_llm_latency,
    observe_llm_tokens,
)


def _value(counter: Any, **labels: str) -> float:
    return counter.labels(**labels)._value.get()  # type: ignore[attr-defined]


def test_gateway_domain_counters_are_registered() -> None:
    """指标名与标签保持迁移前口径(指标端点仍能抓到)。"""
    payload = metrics_payload()
    assert b"# TYPE llm_upstream_errors_total counter" in payload
    assert b"# TYPE llm_tokens_total counter" in payload
    assert b"# TYPE llm_request_duration_seconds histogram" in payload


def test_observe_llm_error_counts_upstream_failure() -> None:
    observe_llm_error(
        provider="openai", code="llm.upstream_error", service="svc-gateway"
    )
    assert (
        _value(
            llm_upstream_errors_total,
            service="svc-gateway",
            provider="openai",
            code="llm.upstream_error",
        )
        == 1
    )
    # 空 provider/code 归一到 unknown,不产生空标签样本
    observe_llm_error(provider="", code="", service="svc-gateway")
    assert (
        _value(
            llm_upstream_errors_total,
            service="svc-gateway",
            provider="unknown",
            code="unknown",
        )
        == 1
    )


def test_observe_llm_tokens_counts_and_skips_zero() -> None:
    observe_llm_tokens(
        model="gpt-test", direction="input", tokens=7, service="svc-gateway"
    )
    observe_llm_tokens(
        model="gpt-test", direction="output", tokens=5, service="svc-gateway"
    )
    assert (
        _value(
            llm_tokens_total,
            service="svc-gateway",
            model="gpt-test",
            direction="input",
        )
        == 7
    )
    assert (
        _value(
            llm_tokens_total,
            service="svc-gateway",
            model="gpt-test",
            direction="output",
        )
        == 5
    )
    # 为 0 的上游不记(避免空样本)
    observe_llm_tokens(
        model="gpt-test", direction="input", tokens=0, service="svc-gateway"
    )
    assert (
        _value(
            llm_tokens_total,
            service="svc-gateway",
            model="gpt-test",
            direction="input",
        )
        == 7
    )


def test_observe_llm_latency_records_seconds() -> None:
    observe_llm_latency(
        provider="openai", model="gpt-test", seconds=1.5, service="svc-gateway"
    )
    histogram = llm_request_duration_seconds.labels(
        service="svc-gateway", provider="openai", model="gpt-test"
    )
    samples = {s.name: s.value for s in histogram.collect()[0].samples}
    assert samples["llm_request_duration_seconds_sum"] >= 1.5
    assert samples["llm_request_duration_seconds_count"] == 1
