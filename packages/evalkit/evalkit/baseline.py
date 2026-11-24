"""基线与行为漂移:把一次报告压成可比对的最小快照。

基线只记录"行为指纹"(通过与否、得分、token、指标、事件集、工具集),
不记录输出正文与耗时——正文会随文案变化产生噪声,耗时在共享 CI 上不稳定。
指标按"绝对变化"判定:方向由用例的 grader 表达(例如冗余度 max),
基线只负责提示"这里变了,需要人确认"。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import BaselineError
from .runner import SuiteRun

BASELINE_VERSION = 1
#: 单个用例 token 相对基线允许的上浮比例。
TOKEN_DRIFT_RATIO = 0.2


@dataclass(frozen=True)
class Drift:
    """一条行为漂移(用例增删、失败、分数/指标/事件/工具变化)。"""

    suite: str
    case_id: str
    kind: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "suite": self.suite,
            "case": self.case_id,
            "kind": self.kind,
            "message": self.message,
        }


def build_baseline(suite_runs: Sequence[SuiteRun]) -> dict[str, Any]:
    """由本次运行生成基线 payload。"""
    suites: list[dict[str, Any]] = []
    for run in suite_runs:
        cases: dict[str, Any] = {}
        for case in run.cases:
            cases[case.case_id] = {
                "ok": case.ok,
                "score": case.score,
                "tokens": case.tokens,
                "metrics": case.metrics,
                "events": sorted(set(case.events)),
                "tool_calls": sorted(set(case.tool_calls)),
            }
        suites.append(
            {
                "name": run.name,
                "dataset": run.dataset,
                "dataset_version": run.dataset_version,
                "cases": cases,
            }
        )
    return {
        "version": BASELINE_VERSION,
        "suites": suites,
    }


def write_baseline(path: str | Path, suite_runs: Sequence[SuiteRun]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_baseline(suite_runs)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_baseline(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BaselineError(f"读取基线失败:{source}:{exc}") from exc
    except json.JSONDecodeError as exc:
        raise BaselineError(f"基线不是合法 JSON:{source}:{exc}") from exc
    if not isinstance(payload, Mapping):
        raise BaselineError(f"基线必须是 JSON 对象:{source}")
    if payload.get("version") != BASELINE_VERSION:
        raise BaselineError(
            f"基线版本不支持:{payload.get('version')!r},当前 {BASELINE_VERSION}"
        )
    if not isinstance(payload.get("suites"), list):
        raise BaselineError(f"基线缺少 suites 列表:{source}")
    return dict(payload)


def compare(
    report: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    token_ratio: float = TOKEN_DRIFT_RATIO,
    metric_tolerance: float = 1e-9,
) -> list[Drift]:
    """比较报告与基线,返回按套件/用例排序的漂移列表。"""
    current_suites = _suite_map(report)
    base_suites = _suite_map(baseline)
    drifts: list[Drift] = []
    for suite_name, base_suite in base_suites.items():
        current_suite = current_suites.get(suite_name)
        if current_suite is None:
            drifts.append(
                Drift(suite_name, "", "suite_removed", "基线中的套件不再运行")
            )
            continue
        current_cases = _case_map(current_suite)
        base_cases = _case_map(base_suite)
        for case_id in sorted(set(base_cases) - set(current_cases)):
            drifts.append(
                Drift(suite_name, case_id, "case_removed", "用例从本次运行消失")
            )
        for case_id in sorted(set(current_cases) - set(base_cases)):
            drifts.append(
                Drift(suite_name, case_id, "case_added", "出现基线中没有的新用例")
            )
        for case_id in sorted(set(base_cases) & set(current_cases)):
            drifts.extend(
                _compare_case(
                    suite_name,
                    case_id,
                    base_cases[case_id],
                    current_cases[case_id],
                    token_ratio=token_ratio,
                    metric_tolerance=metric_tolerance,
                )
            )
    for suite_name in sorted(set(current_suites) - set(base_suites)):
        drifts.append(Drift(suite_name, "", "suite_added", "出现基线中没有的新套件"))
    return drifts


def _compare_case(
    suite_name: str,
    case_id: str,
    base: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    token_ratio: float,
    metric_tolerance: float,
) -> list[Drift]:
    drifts: list[Drift] = []
    if bool(base.get("ok")) and not bool(current.get("ok")):
        drifts.append(Drift(suite_name, case_id, "failed", "上次通过,本次失败"))
    base_score = _number(base.get("score"))
    current_score = _number(current.get("score"))
    if (
        base_score is not None
        and current_score is not None
        and current_score + metric_tolerance < base_score
    ):
        drifts.append(
            Drift(
                suite_name,
                case_id,
                "score_drop",
                f"得分从 {base_score:.4f} 降到 {current_score:.4f}",
            )
        )
    base_tokens = _number(base.get("tokens"))
    current_tokens = _number(current.get("tokens"))
    if base_tokens and current_tokens is not None:
        limit = base_tokens * (1.0 + token_ratio)
        if current_tokens > limit:
            drifts.append(
                Drift(
                    suite_name,
                    case_id,
                    "token_increase",
                    f"token 从 {int(base_tokens)} 增到 {int(current_tokens)}"
                    f"(超过 {token_ratio:.0%})",
                )
            )
    drifts.extend(
        _compare_metrics(
            suite_name,
            case_id,
            base.get("metrics"),
            current.get("metrics"),
            tolerance=metric_tolerance,
        )
    )
    drifts.extend(
        _compare_set(
            suite_name,
            case_id,
            "events",
            base.get("events"),
            current.get("events"),
        )
    )
    drifts.extend(
        _compare_set(
            suite_name,
            case_id,
            "tool_calls",
            base.get("tool_calls"),
            current.get("tool_calls"),
        )
    )
    return drifts


def _compare_metrics(
    suite_name: str,
    case_id: str,
    base: Any,
    current: Any,
    *,
    tolerance: float,
) -> list[Drift]:
    if not isinstance(base, Mapping):
        return []
    current_map = current if isinstance(current, Mapping) else {}
    drifts: list[Drift] = []
    for key in sorted(base):
        base_value = _number(base[key])
        current_value = _number(current_map.get(key))
        if base_value is None:
            continue
        if current_value is None:
            drifts.append(
                Drift(
                    suite_name,
                    case_id,
                    f"metric_missing:{key}",
                    f"指标 {key} 从基线消失",
                )
            )
        elif abs(current_value - base_value) > tolerance:
            drifts.append(
                Drift(
                    suite_name,
                    case_id,
                    f"metric_changed:{key}",
                    f"指标 {key} 从 {base_value:.4f} 变为 {current_value:.4f}",
                )
            )
    return drifts


def _compare_set(
    suite_name: str,
    case_id: str,
    label: str,
    base: Any,
    current: Any,
) -> list[Drift]:
    base_set = {str(item) for item in base} if isinstance(base, list) else set()
    current_set = (
        {str(item) for item in current} if isinstance(current, list) else set()
    )
    added = sorted(current_set - base_set)
    removed = sorted(base_set - current_set)
    if not added and not removed:
        return []
    parts = []
    if added:
        parts.append(f"+{added}")
    if removed:
        parts.append(f"-{removed}")
    return [
        Drift(
            suite_name,
            case_id,
            f"{label}_changed",
            f"{label} 集合变化:{', '.join(parts)}",
        )
    ]


def _suite_map(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    suites = payload.get("suites")
    if not isinstance(suites, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for suite in suites:
        if isinstance(suite, Mapping) and isinstance(suite.get("name"), str):
            result[str(suite["name"])] = suite
    return result


def _case_map(suite: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    cases = suite.get("cases")
    if isinstance(cases, Mapping):
        return {
            str(case_id): case
            for case_id, case in cases.items()
            if isinstance(case, Mapping)
        }
    if isinstance(cases, list):
        result: dict[str, Mapping[str, Any]] = {}
        for case in cases:
            if isinstance(case, Mapping) and isinstance(case.get("id"), str):
                result[str(case["id"])] = case
        return result
    return {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
