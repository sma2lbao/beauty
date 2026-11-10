"""用例断言:把采集到的指标与用例 expect 比对(设计 §2.2)。

只做纯数据比对,不碰 HTTP/DB;每条失败原因用中文写清"期望 vs 实际",
CLI 表格与 JSON 报告都直接展示这组字符串。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class CaseMetrics(Protocol):
    """断言只依赖这几个只读字段(runner 的 CaseResult 满足它)。"""

    run_status: str | None
    events: list[str]
    tool_calls: list[str]
    deliverables: int
    tokens: int
    reply: str


def _strings(value: Any) -> list[str]:
    """任意值 -> 字符串列表(单个字符串也接受,便于用例写简写)。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    return [str(value)]


def _check_subset(expected: Any, actual: list[str], label: str) -> list[str]:
    missing = [name for name in _strings(expected) if name not in actual]
    if not missing:
        return []
    return [f"{label}缺少 {missing}(实际:{actual})"]


def evaluate(expect: Mapping[str, Any], metrics: CaseMetrics) -> list[str]:
    """返回失败原因列表;空列表表示该用例全部断言通过。"""
    failures: list[str] = []
    if "run_status" in expect:
        expected = expect["run_status"]
        if metrics.run_status != expected:
            failures.append(f"run 状态期望 {expected!r},实际 {metrics.run_status!r}")

    events = list(metrics.events)
    failures.extend(_check_subset(expect.get("events_include"), events, "事件序列"))
    forbidden = [
        name for name in _strings(expect.get("events_exclude")) if name in events
    ]
    if forbidden:
        failures.append(f"出现了不应出现的事件 {forbidden}")

    failures.extend(
        _check_subset(expect.get("tool_calls"), metrics.tool_calls, "工具调用")
    )

    needle = expect.get("reply_contains")
    if needle is not None and str(needle) not in metrics.reply:
        failures.append(f"回复不包含 {str(needle)!r}(实际回复:{metrics.reply!r})")

    minimum = expect.get("min_events")
    if minimum is not None and len(events) < int(minimum):
        failures.append(f"事件数期望至少 {int(minimum)} 条,实际 {len(events)} 条")

    maximum = expect.get("max_events")
    if maximum is not None and len(events) > int(maximum):
        failures.append(f"事件数期望最多 {int(maximum)} 条,实际 {len(events)} 条")

    if "deliverables" in expect:
        expected_count = expect["deliverables"]
        if not isinstance(expected_count, int) or isinstance(expected_count, bool):
            failures.append(f"deliverables 断言必须是整数,实际 {expected_count!r}")
        elif expected_count < 0:
            failures.append(f"deliverables 不能为负:{expected_count}")
        elif metrics.deliverables != expected_count:
            failures.append(
                f"交付物数量期望 {expected_count},实际 {metrics.deliverables}"
            )

    token_limit = expect.get("max_total_tokens")
    if token_limit is not None and metrics.tokens > int(token_limit):
        failures.append(
            f"token 用量期望不超过 {int(token_limit)},实际 {metrics.tokens}"
        )
    return failures
