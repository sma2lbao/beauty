"""套件执行器:加载数据集、驱动 adapter、评分并记录耗时。

平台不并发执行用例:离线评测的确定性优先,单用例异常只落成该用例失败,
不中断同一套件的其它用例。adapter 负责把外部依赖(模型、数据库、沙箱)
钉成 Fake/录制回放;平台只约束"零网络由 adapter 证明"。
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .adapters import Adapter, load_adapter
from .config import PlatformConfig, SuiteConfig, select_suites
from .errors import ConfigError, RunnerError
from .graders import CaseGrade, Observation, grade_case
from .schema import Case, Dataset, load_dataset


@dataclass(frozen=True)
class CaseRun:
    """一条用例的执行结果(报告与基线共用)。"""

    suite: str
    case_id: str
    group: str
    tags: tuple[str, ...]
    ok: bool
    score: float
    duration_ms: float
    output: str
    error: str | None
    outcomes: tuple[Mapping[str, Any], ...]
    fields: Mapping[str, Any]
    events: tuple[str, ...]
    tool_calls: tuple[str, ...]

    @property
    def tokens(self) -> int:
        value = self.fields.get("tokens", 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return int(value)

    @property
    def metrics(self) -> dict[str, float]:
        raw = self.fields.get("metrics")
        if not isinstance(raw, Mapping):
            return {}
        result: dict[str, float] = {}
        for key, value in raw.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            result[str(key)] = float(value)
        return result

    def failures(self) -> tuple[str, ...]:
        outcomes = [
            outcome for outcome in self.outcomes if outcome.get("required", True)
        ]
        messages = [
            str(outcome.get("message", ""))
            for outcome in outcomes
            if not outcome.get("ok")
        ]
        if self.error:
            messages.insert(0, f"执行错误:{self.error}")
        return tuple(message for message in messages if message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "group": self.group,
            "tags": list(self.tags),
            "ok": self.ok,
            "score": self.score,
            "duration_ms": round(self.duration_ms, 2),
            "tokens": self.tokens,
            "output": self.output,
            "error": self.error,
            "fields": dict(self.fields),
            "events": list(self.events),
            "tool_calls": list(self.tool_calls),
            "metrics": self.metrics,
            "graders": [dict(outcome) for outcome in self.outcomes],
            "failures": list(self.failures()),
        }


@dataclass(frozen=True)
class SuiteRun:
    """一个套件的执行结果。"""

    name: str
    dataset: str
    dataset_version: int
    cases: tuple[CaseRun, ...]
    duration_ms: float
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    @property
    def totals(self) -> dict[str, Any]:
        total = len(self.cases)
        passed = sum(1 for case in self.cases if case.ok)
        score = sum(case.score for case in self.cases) / total if total else 0.0
        return {
            "cases": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "score": round(score, 4),
            "tokens": sum(case.tokens for case in self.cases),
            "duration_ms": round(self.duration_ms, 2),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dataset": self.dataset,
            "dataset_version": self.dataset_version,
            "generated_at": self.generated_at,
            "totals": self.totals,
            "cases": [case.to_dict() for case in self.cases],
        }


def run_suite(
    config: SuiteConfig,
    *,
    adapter: Adapter | None = None,
    tags: Sequence[str] = (),
    case_ids: Sequence[str] = (),
) -> SuiteRun:
    """执行一个套件;adapter 为空时按配置/清单加载。"""
    dataset = load_dataset(config.dataset)
    target = config.adapter or dataset.manifest.adapter
    owned_adapter = adapter is None
    if adapter is None:
        adapter = load_adapter(target, config.options)
    selected = _select_cases(dataset, tags=tags, case_ids=case_ids)
    if not selected:
        raise RunnerError(
            f"套件 {config.name} 没有匹配的用例(tags={list(tags)},"
            f"cases={list(case_ids)})"
        )
    started = time.perf_counter()
    cases: list[CaseRun] = []
    try:
        for case in selected:
            cases.append(_run_case(config.name, case, adapter))
    finally:
        if owned_adapter:
            with contextlib.suppress(Exception):
                adapter.close()
    duration_ms = (time.perf_counter() - started) * 1000
    return SuiteRun(
        name=config.name,
        dataset=dataset.manifest.name,
        dataset_version=dataset.manifest.version,
        cases=tuple(cases),
        duration_ms=duration_ms,
    )


def run_suites(
    config: PlatformConfig,
    *,
    suite_names: Sequence[str] = (),
    tags: Sequence[str] = (),
    case_ids: Sequence[str] = (),
) -> tuple[SuiteRun, ...]:
    """按配置顺序执行所选套件。"""
    suites = select_suites(config, tuple(suite_names))
    return tuple(run_suite(suite, tags=tags, case_ids=case_ids) for suite in suites)


def _run_case(suite_name: str, case: Case, adapter: Adapter) -> CaseRun:
    started = time.perf_counter()
    try:
        observation = adapter.run(case)
        if not isinstance(observation, Observation):
            raise TypeError(
                f"adapter 返回 {type(observation).__name__},需要 Observation"
            )
    except Exception as exc:  # 单用例异常必须落成失败,不中断套件
        observation = Observation(error=f"{type(exc).__name__}: {exc}")
    grade = grade_case(case, observation)
    duration_ms = (time.perf_counter() - started) * 1000
    return _case_run(suite_name, case, observation, grade, duration_ms)


def _case_run(
    suite_name: str,
    case: Case,
    observation: Observation,
    grade: CaseGrade,
    duration_ms: float,
) -> CaseRun:
    return CaseRun(
        suite=suite_name,
        case_id=case.id,
        group=case.group,
        tags=case.tags,
        ok=grade.ok,
        score=grade.score,
        duration_ms=duration_ms,
        output=observation.output,
        error=observation.error,
        outcomes=tuple(outcome.to_dict() for outcome in grade.outcomes),
        fields=dict(observation.fields),
        events=tuple(observation.events),
        tool_calls=tuple(observation.tool_calls),
    )


def _select_cases(
    dataset: Dataset,
    *,
    tags: Sequence[str],
    case_ids: Sequence[str],
) -> tuple[Case, ...]:
    wanted_tags = set(tags)
    wanted_ids = set(case_ids)
    selected = [
        case
        for case in dataset.cases
        if (not wanted_tags or wanted_tags.intersection(case.tags))
        and (not wanted_ids or case.id in wanted_ids)
    ]
    unknown_ids = wanted_ids - {case.id for case in dataset.cases}
    if unknown_ids:
        raise ConfigError(f"数据集里没有用例 {sorted(unknown_ids)}")
    return tuple(selected)
