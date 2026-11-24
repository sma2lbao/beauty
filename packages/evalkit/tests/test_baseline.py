"""基线快照与行为漂移:只报需要人确认的变化。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalkit.baseline import (
    BASELINE_VERSION,
    Drift,
    build_baseline,
    compare,
    load_baseline,
    write_baseline,
)
from evalkit.errors import BaselineError
from evalkit.runner import CaseRun, SuiteRun


def case_payload(
    *,
    ok: bool = True,
    score: float = 1.0,
    tokens: int = 10,
    metrics: dict | None = None,
    events: list | None = None,
    tool_calls: list | None = None,
) -> dict:
    return {
        "ok": ok,
        "score": score,
        "tokens": tokens,
        "metrics": metrics or {},
        "events": events or [],
        "tool_calls": tool_calls or [],
    }


def report(
    cases: dict[str, dict] | None = None,
    *,
    suite: str = "demo",
) -> dict:
    return {"suites": [{"name": suite, "cases": cases or {}}]}


def kinds(drifts: list[Drift]) -> list[str]:
    return [drift.kind for drift in drifts]


def test_identical_is_clean() -> None:
    payload = report({"demo/a": case_payload()})
    assert compare(payload, payload) == []


def test_case_and_suite_add_remove() -> None:
    base = report({"demo/a": case_payload()})
    current = report({"demo/b": case_payload()})
    assert kinds(compare(current, base)) == [
        "case_removed",
        "case_added",
    ]
    both = {
        "suites": [
            {"name": "demo", "cases": {"demo/a": case_payload()}},
            {"name": "other", "cases": {}},
        ]
    }
    assert "suite_added" in kinds(compare(both, base))
    assert "suite_removed" in kinds(compare(base, both))


def test_failed_and_score_drop() -> None:
    base = report({"demo/a": case_payload(ok=True, score=1.0)})
    current = report({"demo/a": case_payload(ok=False, score=0.5)})
    drifts = compare(current, base)
    assert kinds(drifts) == ["failed", "score_drop"]
    assert "上次通过" in drifts[0].message
    assert "0.5000" in drifts[1].message


def test_score_increase_is_not_drift() -> None:
    base = report({"demo/a": case_payload(score=0.5)})
    current = report({"demo/a": case_payload(score=0.75)})
    assert compare(current, base) == []


def test_token_threshold_is_strictly_greater() -> None:
    base = report({"demo/a": case_payload(tokens=100)})
    assert compare(report({"demo/a": case_payload(tokens=120)}), base) == []
    drift = compare(report({"demo/a": case_payload(tokens=121)}), base)
    assert kinds(drift) == ["token_increase"]
    assert "20%" in drift[0].message


def test_zero_base_token_is_ignored() -> None:
    base = report({"demo/a": case_payload(tokens=0)})
    assert compare(report({"demo/a": case_payload(tokens=999)}), base) == []


def test_metrics_changed_and_missing() -> None:
    base = report({"demo/a": case_payload(metrics={"recall": 1.0, "redundancy": 0.1})})
    current = report({"demo/a": case_payload(metrics={"recall": 0.9})})
    drifts = compare(current, base)
    assert kinds(drifts) == [
        "metric_changed:recall",
        "metric_missing:redundancy",
    ]
    assert "0.9000" in drifts[0].message


def test_metric_increase_also_reported() -> None:
    base = report({"demo/a": case_payload(metrics={"redundancy": 0.1})})
    current = report({"demo/a": case_payload(metrics={"redundancy": 0.2})})
    assert kinds(compare(current, base)) == ["metric_changed:redundancy"]


def test_metric_tolerance_and_non_numeric() -> None:
    base = report({"demo/a": case_payload(metrics={"recall": 1.0})})
    current = report({"demo/a": case_payload(metrics={"recall": 1.0 + 1e-10})})
    assert compare(current, base) == []
    current = report({"demo/a": case_payload(metrics={"recall": "n/a"})})
    assert kinds(compare(current, base)) == ["metric_missing:recall"]


def test_events_and_tool_calls() -> None:
    base = report({"demo/a": case_payload(events=["a"], tool_calls=["search"])})
    current = report(
        {"demo/a": case_payload(events=["a", "b"], tool_calls=["search", "write"])}
    )
    drifts = compare(current, base)
    assert kinds(drifts) == ["events_changed", "tool_calls_changed"]
    assert "+['b']" in drifts[0].message
    assert "+['write']" in drifts[1].message


def test_build_baseline_sorts_and_dedups() -> None:
    case = CaseRun(
        suite="demo",
        case_id="demo/a",
        group="default",
        tags=(),
        ok=True,
        score=1.0,
        duration_ms=1.0,
        output="ignored",
        error=None,
        outcomes=(),
        fields={"tokens": 7, "metrics": {"m": 1.0}},
        events=("b", "a", "b"),
        tool_calls=("write", "search"),
    )
    run = SuiteRun(
        name="demo",
        dataset="dataset",
        dataset_version=2,
        cases=(case,),
        duration_ms=10.0,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    payload = build_baseline([run])
    assert payload["version"] == BASELINE_VERSION
    suite = payload["suites"][0]
    assert suite["dataset_version"] == 2
    stored = suite["cases"]["demo/a"]
    assert stored["events"] == ["a", "b"]
    assert stored["tool_calls"] == ["search", "write"]
    assert stored["tokens"] == 7


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    run = SuiteRun(
        name="demo",
        dataset="dataset",
        dataset_version=1,
        cases=(),
        duration_ms=1.0,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    path = tmp_path / "nested" / "base.json"
    write_baseline(path, [run])
    loaded = load_baseline(path)
    assert loaded["version"] == BASELINE_VERSION
    assert loaded["suites"][0]["name"] == "demo"


def test_load_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(BaselineError, match="读取基线失败"):
        load_baseline(missing)
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(BaselineError, match="不是合法 JSON"):
        load_baseline(bad)
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(BaselineError, match="必须是 JSON 对象"):
        load_baseline(bad)
    bad.write_text(json.dumps({"version": 99, "suites": []}), encoding="utf-8")
    with pytest.raises(BaselineError, match="版本不支持"):
        load_baseline(bad)
    bad.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(BaselineError, match="缺少 suites"):
        load_baseline(bad)


def test_drift_to_dict() -> None:
    drift = Drift("s", "c", "failed", "msg")
    assert drift.to_dict() == {
        "suite": "s",
        "case": "c",
        "kind": "failed",
        "message": "msg",
    }
