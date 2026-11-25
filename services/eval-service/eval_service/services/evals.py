"""评测业务逻辑:报告归一化、漂移计算、索引与分页。

报告原样入库后，服务把基线漂移**回填进报告快照**（drifts/suite.drift_count/
case.baseline_diff），这样运行详情只读一次库就能拿到完整结论；基线本身仍是
独立的已批准记录，不随运行变化。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from common.errors import AppError
from evalkit.baseline import compare
from sqlalchemy.ext.asyncio import AsyncSession

from eval_service.models import EvalBaseline, EvalCaseRun, EvalRun, EvalSuiteRun
from eval_service.repositories import (
    EvalBaselineRepository,
    EvalCaseRunRepository,
    EvalRunRepository,
    EvalSuiteRunRepository,
    json_list,
    json_object,
)
from eval_service.schemas.evals import (
    EvalBaselineRead,
    EvalCasePageRead,
    EvalCaseRead,
    EvalIndexRead,
    EvalRunRead,
    EvalRunUpload,
    EvalSuiteSummaryRead,
    EvalTotalsRead,
)


class BadReportError(AppError):
    """上报的报告结构不合法。"""

    status = 400


def _bad(message: str) -> BadReportError:
    return BadReportError("eval.report_invalid", message)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return _dump(value)


def _suites(report: dict[str, Any]) -> list[dict[str, Any]]:
    raw = report.get("suites")
    if not isinstance(raw, list):
        raise _bad("report.suites 必须是数组")
    suites = [suite for suite in raw if isinstance(suite, dict)]
    if len(suites) != len(raw):
        raise _bad("report.suites 中存在非对象项")
    if not suites:
        raise _bad("report.suites 不能为空")
    return suites


def _cases(suite: dict[str, Any]) -> list[dict[str, Any]]:
    raw = suite.get("cases")
    if not isinstance(raw, list):
        raise _bad(f"套件 {suite.get('name')!r} 的 cases 必须是数组")
    cases = [case for case in raw if isinstance(case, dict)]
    if len(cases) != len(raw):
        raise _bad(f"套件 {suite.get('name')!r} 的 cases 中存在非对象项")
    return cases


def _totals(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    passed = sum(1 for case in cases if bool(case.get("ok")))
    score = sum(_number(case.get("score")) for case in cases) / total if total else 0.0
    return {
        "cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "score": round(score, 4),
        "tokens": sum(_integer(case.get("tokens")) for case in cases),
        "duration_ms": round(
            sum(_number(case.get("duration_ms")) for case in cases), 2
        ),
    }


def _failed_cases(suite_name: str, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for case in cases:
        if bool(case.get("ok")):
            continue
        failures = case.get("failures")
        messages = (
            [str(item) for item in failures] if isinstance(failures, list) else []
        )
        if not messages and case.get("error"):
            messages = [f"执行错误:{case['error']}"]
        failed.append(
            {
                "suite": suite_name,
                "case_id": str(case.get("id", "")),
                "failures": messages,
            }
        )
    return failed


def _baseline_for_suite(
    suite: dict[str, Any], body: EvalRunUpload, approved: dict[str, EvalBaseline]
) -> dict[str, Any] | None:
    name = str(suite.get("name", ""))
    payload = body.baselines.get(name)
    if payload is None:
        record = approved.get(name)
        if record is not None:
            try:
                payload = json.loads(record.payload_json)
            except ValueError:
                payload = None
    if payload is None:
        return None
    # 只比较当前套件；基线文件按套件拆分，避免把兄弟套件误报成 suite_removed。
    filtered = {
        **payload,
        "suites": [
            item
            for item in payload.get("suites", [])
            if isinstance(item, dict) and item.get("name") == name
        ],
    }
    if not filtered["suites"]:
        return None
    return filtered


def _drifts_for(
    suite: dict[str, Any], baseline: dict[str, Any] | None
) -> list[dict[str, str]]:
    if baseline is None:
        return []
    mini = {
        "version": 1,
        "generated_at": "",
        "totals": {},
        "suites": [suite],
    }
    try:
        return [drift.to_dict() for drift in compare(mini, baseline)]
    except Exception as exc:  # noqa: BLE001 - 基线结构错误要变成 400,而非 500
        raise BadReportError(
            "eval.baseline_invalid",
            f"基线结构不合法:{exc}",
        ) from exc


async def ingest_run(session: AsyncSession, body: EvalRunUpload) -> EvalRun:
    """入库一次运行；相同 external_id 幂等返回已有记录。"""

    runs = EvalRunRepository(session)
    if body.external_id:
        existing = await runs.find_external(body.external_id)
        if existing is not None:
            return existing

    report = dict(body.report)
    suites = _suites(report)
    approved = {
        record.suite: record
        for record in await EvalBaselineRepository(session).list_all()
    }

    suite_rows: list[EvalSuiteRun] = []
    case_rows: list[EvalCaseRun] = []
    suite_summaries: list[dict[str, Any]] = []
    failed_cases: list[dict[str, Any]] = []
    dataset_versions: dict[str, int | None] = {}
    all_drifts: list[dict[str, str]] = []
    total_cases = 0
    total_passed = 0
    total_tokens = 0
    total_duration = 0.0
    total_score = 0.0

    for suite in suites:
        name = str(suite.get("name", "")).strip()
        if not name:
            raise _bad("套件缺少 name")
        cases = _cases(suite)
        totals = _totals(cases)
        baseline = _baseline_for_suite(suite, body, approved)
        drifts = _drifts_for(suite, baseline)
        case_drifts: dict[str, list[dict[str, str]]] = defaultdict(list)
        for drift in drifts:
            case_drifts[drift["case"]].append(drift)
        suite["drift_count"] = len(drifts)
        for case in cases:
            case["baseline_diff"] = case_drifts.get(str(case.get("id", "")), [])

        dataset_version = suite.get("dataset_version")
        version = (
            int(dataset_version)
            if isinstance(dataset_version, int)
            and not isinstance(dataset_version, bool)
            else None
        )
        dataset_versions[name] = version
        failed_cases.extend(_failed_cases(name, cases))
        all_drifts.extend(drifts)
        total_cases += totals["cases"]
        total_passed += totals["passed"]
        total_tokens += totals["tokens"]
        total_duration += totals["duration_ms"]
        total_score += sum(_number(case.get("score")) for case in cases)

        summary = {
            "name": name,
            "dataset": suite.get("dataset"),
            "dataset_version": version,
            "totals": totals,
            "drift_count": len(drifts),
        }
        suite_summaries.append(summary)
        suite_rows.append(
            EvalSuiteRun(
                run_id="",  # 占位，run flush 后回填
                name=name,
                dataset=suite.get("dataset"),
                dataset_version=version,
                totals_json=_dump(totals),
                drifts_json=_dump(drifts),
                drift_count=len(drifts),
            )
        )
        for case in cases:
            case_id = str(case.get("id", "")).strip()
            if not case_id:
                raise _bad(f"套件 {name!r} 的用例缺少 id")
            case_rows.append(
                EvalCaseRun(
                    run_id="",
                    suite=name,
                    case_id=case_id,
                    group=str(case.get("group") or ""),
                    tags_json=_dump(case.get("tags") or []),
                    ok=bool(case.get("ok")),
                    score=_number(case.get("score")),
                    duration_ms=_number(case.get("duration_ms")),
                    tokens=_integer(case.get("tokens")),
                    output=_text(case.get("output")),
                    error=(_text(case.get("error")) or None),
                    fields_json=_dump(case.get("fields") or {}),
                    events_json=_dump(case.get("events") or []),
                    tool_calls_json=_dump(case.get("tool_calls") or []),
                    metrics_json=_dump(case.get("metrics") or {}),
                    graders_json=_dump(case.get("graders") or []),
                    failures_json=_dump(case.get("failures") or []),
                    baseline_diff_json=_dump(case.get("baseline_diff") or []),
                )
            )

    report["drifts"] = all_drifts
    report["totals"] = {
        "cases": total_cases,
        "passed": total_passed,
        "failed": total_cases - total_passed,
        "pass_rate": (round(total_passed / total_cases, 4) if total_cases else 0.0),
        "score": round(total_score / total_cases, 4) if total_cases else 0.0,
        "tokens": total_tokens,
        "duration_ms": round(total_duration, 2),
    }

    run = EvalRun(
        external_id=body.external_id,
        trigger=body.trigger,
        git_sha=body.git_sha,
        branch=body.branch,
        pr=body.pr,
        model=body.model,
        environment=body.environment,
        report_version=_integer(report.get("version"), 1),
        report_json=_dump(report),
        totals_json=_dump(report["totals"]),
        suites_json=_dump(suite_summaries),
        failed_cases_json=_dump(failed_cases),
        dataset_versions_json=_dump(dataset_versions),
        drifts_json=_dump(all_drifts),
        drift_count=len(all_drifts),
        failed_count=report["totals"]["failed"],
    )
    session.add(run)
    await session.flush()

    for row in suite_rows + case_rows:
        row.run_id = str(run.id)
        session.add(row)
    await session.flush()
    return run


def run_to_read(run: EvalRun) -> EvalRunRead:
    suites = [
        EvalSuiteSummaryRead.model_validate(item)
        for item in json_list(run.suites_json)
        if isinstance(item, dict)
    ]
    return EvalRunRead(
        id=str(run.id),
        created_at=run.created_at,
        trigger=run.trigger,
        git_sha=run.git_sha,
        branch=run.branch,
        pr=run.pr,
        model=run.model,
        environment=run.environment,
        report_version=run.report_version,
        totals=EvalTotalsRead.model_validate(json_object(run.totals_json)),
        drift_count=run.drift_count,
        suites=suites,
        failed_cases=json_list(run.failed_cases_json),
        dataset_versions=json_object(run.dataset_versions_json),
    )


async def load_index(
    session: AsyncSession,
    *,
    suite: str | None = None,
    status: str | None = None,
    branch: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> EvalIndexRead:
    runs = EvalRunRepository(session)
    rows = await runs.list_page(
        suite=suite,
        status=status,
        branch=branch,
        limit=limit,
        offset=offset,
    )
    return EvalIndexRead(
        version=1,
        generated_at=datetime.now(UTC),
        runs=[run_to_read(row) for row in rows],
    )


async def load_report(session: AsyncSession, run_id: str) -> dict[str, Any]:
    run = await EvalRunRepository(session).require(run_id)
    try:
        report = json.loads(run.report_json)
    except ValueError as exc:  # pragma: no cover - 只有写坏的行才会触发
        raise _bad("报告内容损坏") from exc
    return report if isinstance(report, dict) else {}


async def load_case_page(
    session: AsyncSession,
    run_id: str,
    suite: str,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> EvalCasePageRead:
    await EvalRunRepository(session).require(run_id)
    await EvalSuiteRunRepository(session).get_for_run(run_id, suite) or _missing_suite(
        suite
    )
    repository = EvalCaseRunRepository(session)
    rows = await repository.list_page(
        run_id, suite, status=status, limit=limit, offset=offset
    )
    total = await repository.count_page(run_id, suite, status=status)
    return EvalCasePageRead(
        items=[_case_to_read(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _missing_suite(suite: str) -> None:
    raise AppError(
        "eval.suite_not_found",
        f"套件 {suite} 不存在",
        details={"suite": suite},
    )


def _case_to_read(row: EvalCaseRun) -> EvalCaseRead:
    return EvalCaseRead(
        id=row.case_id,
        group=row.group,
        tags=json_list(row.tags_json),
        ok=row.ok,
        score=row.score,
        duration_ms=row.duration_ms,
        tokens=row.tokens,
        output=row.output,
        error=row.error,
        fields=json_object(row.fields_json),
        events=json_list(row.events_json),
        tool_calls=json_list(row.tool_calls_json),
        metrics=json_object(row.metrics_json),
        graders=json_list(row.graders_json),
        failures=json_list(row.failures_json),
        baseline_diff=json_list(row.baseline_diff_json),
    )


async def approve_baseline(
    session: AsyncSession,
    suite: str,
    *,
    payload: dict[str, Any],
    dataset_version: int | None,
    evaluator_version: str,
    git_sha: str,
    approved_by: str,
) -> EvalBaseline:
    return await EvalBaselineRepository(session).upsert(
        suite=suite,
        payload_json=_dump(payload),
        dataset_version=dataset_version,
        evaluator_version=evaluator_version,
        git_sha=git_sha,
        approved_by=approved_by,
    )


async def list_baselines(session: AsyncSession) -> list[EvalBaselineRead]:
    records = await EvalBaselineRepository(session).list_all()
    return [_baseline_to_read(record) for record in records]


async def get_baseline_payload(session: AsyncSession, suite: str) -> dict[str, Any]:
    record = await EvalBaselineRepository(session).get_by_suite(suite)
    if record is None:
        raise AppError(
            "eval.baseline_not_found",
            f"套件 {suite} 还没有已批准基线",
            details={"suite": suite},
        )
    try:
        payload = json.loads(record.payload_json)
    except ValueError as exc:  # pragma: no cover - 只有写坏的行才会触发
        raise _bad("基线内容损坏") from exc
    return payload if isinstance(payload, dict) else {}


def _baseline_to_read(record: EvalBaseline) -> EvalBaselineRead:
    return EvalBaselineRead(
        suite=record.suite,
        dataset_version=record.dataset_version,
        evaluator_version=record.evaluator_version,
        git_sha=record.git_sha,
        approved_by=record.approved_by,
        approved_at=record.approved_at,
    )
