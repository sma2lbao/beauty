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
from uuid import uuid4

from common.artifacts import ArtifactStore, build_artifact_key
from common.errors import AppError
from evalkit.baseline import compare
from sqlalchemy.ext.asyncio import AsyncSession

from eval_service.core.errors import (
    CaseNotFoundError,
    ReportCorruptError,
    ReportMissingError,
    ReportStoreError,
    ReportStoreUnavailableError,
)
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
    EvalCompareCaseRead,
    EvalCompareRead,
    EvalCompareSuiteRead,
    EvalCompareTotalsRead,
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


def _summarized_report(report: dict[str, Any]) -> dict[str, Any]:
    """去掉用例明细的报告摘要；完整内容在对象存储里。"""

    suites: list[dict[str, Any]] = []
    for suite in _suites(report):
        cases = _cases(suite)
        summarized = {key: value for key, value in suite.items() if key != "cases"}
        summarized["case_count"] = len(cases)
        suites.append(summarized)
    return {**report, "suites": suites}


async def ingest_run(
    session: AsyncSession,
    body: EvalRunUpload,
    *,
    store: ArtifactStore | None = None,
    inline_limit: int = 64 * 1024,
) -> EvalRun:
    """入库一次运行；相同 external_id 幂等返回已有记录。

    报告序列化后超过 ``inline_limit`` 且有存储时，原始报告转存对象存储，
    DB 只留不含用例明细的摘要；小报告仍内联，读路径对两者都透明。
    """

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

    run_id = uuid4().hex
    report_json = _dump(report)
    report_size = len(report_json.encode("utf-8"))
    report_key: str | None = None
    if store is not None and report_size > inline_limit:
        report_key = build_artifact_key("eval-runs", run_id, suffix=".json")
        await store.put(
            report_key,
            report_json.encode("utf-8"),
            content_type="application/json",
        )
        # DB 留摘要：列表/详情头部仍可直接读，用例明细按需查表。
        report_json = _dump(_summarized_report(report))

    run = EvalRun(
        id=run_id,
        external_id=body.external_id,
        trigger=body.trigger,
        git_sha=body.git_sha,
        branch=body.branch,
        pr=body.pr,
        model=body.model,
        environment=body.environment,
        report_version=_integer(report.get("version"), 1),
        report_json=report_json,
        report_key=report_key,
        report_size=report_size,
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


def _report_of(run: EvalRun) -> dict[str, Any]:
    try:
        report = json.loads(run.report_json)
    except ValueError:  # pragma: no cover - 只有写坏的行才会触发
        return {}
    return report if isinstance(report, dict) else {}


async def _report_payload(run: EvalRun, store: ArtifactStore | None) -> dict[str, Any]:
    """读完整报告:优先对象存储，旧行仍回落到内联 report_json。"""

    if not run.report_key:
        return _report_of(run)
    if store is None:
        raise ReportStoreUnavailableError(
            "eval.report_store_unavailable",
            "运行报告存放在对象存储，但当前实例未装配存储",
        )
    try:
        data = await store.get(run.report_key)
    except Exception as exc:  # noqa: BLE001 - 存储异常统一转成可读的上游错误
        raise ReportStoreError(
            "eval.report_store_error",
            f"读取运行报告失败:{exc}",
        ) from exc
    if data is None:
        raise ReportMissingError(
            "eval.report_missing",
            "运行报告对象已丢失或超过保留期",
        )
    try:
        report = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReportCorruptError(
            "eval.report_invalid",
            "对象存储中的运行报告已损坏",
        ) from exc
    return report if isinstance(report, dict) else {}


def _suite_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = report.get("suites")
    if not isinstance(raw, list):
        return {}
    return {
        str(item.get("name")): item
        for item in raw
        if isinstance(item, dict) and item.get("name")
    }


def _case_map(suite: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if suite is None:
        return {}
    raw = suite.get("cases")
    if not isinstance(raw, list):
        return {}
    return {
        str(item.get("id")): item
        for item in raw
        if isinstance(item, dict) and item.get("id")
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _case_failures(case: dict[str, Any] | None) -> list[str]:
    if case is None:
        return []
    failures = _string_list(case.get("failures"))
    if failures:
        return failures
    error = case.get("error")
    return [f"执行错误:{error}"] if error else []


def _case_status(base: dict[str, Any] | None, candidate: dict[str, Any] | None) -> str:
    if base is None:
        return "added"
    if candidate is None:
        return "removed"
    base_ok = bool(base.get("ok"))
    candidate_ok = bool(candidate.get("ok"))
    if base_ok and not candidate_ok:
        return "regressed"
    if not base_ok and candidate_ok:
        return "fixed"
    return "unchanged"


async def compare_runs(
    session: AsyncSession,
    base_id: str,
    candidate_id: str,
    *,
    store: ArtifactStore | None = None,
) -> EvalCompareRead:
    """按用例对比两次运行:回归/修复/新增/移除 + 通过率与成本差值。

    差异在读取时从报告快照计算，不落表；同一对运行重复对比结果稳定。
    用例以 candidate 的顺序为主，仅存在于 base 的用例追加在套件末尾。
    """

    runs = EvalRunRepository(session)
    base_run = await runs.require(base_id)
    candidate_run = await runs.require(candidate_id)
    base_report = await _report_payload(base_run, store)
    candidate_report = await _report_payload(candidate_run, store)
    base_suites = _suite_map(base_report)
    candidate_suites = _suite_map(candidate_report)

    suite_order = list(candidate_suites)
    suite_order.extend(name for name in base_suites if name not in candidate_suites)

    counts = {
        "regressed": 0,
        "fixed": 0,
        "added": 0,
        "removed": 0,
        "unchanged": 0,
    }
    suite_rows: list[EvalCompareSuiteRead] = []
    case_rows: list[EvalCompareCaseRead] = []

    for name in suite_order:
        base_suite = base_suites.get(name)
        candidate_suite = candidate_suites.get(name)
        base_cases = _case_map(base_suite)
        candidate_cases = _case_map(candidate_suite)
        case_order = list(candidate_cases)
        case_order.extend(
            case_id for case_id in base_cases if case_id not in candidate_cases
        )

        suite_counts = dict.fromkeys(counts, 0)
        for case_id in case_order:
            base_case = base_cases.get(case_id)
            candidate_case = candidate_cases.get(case_id)
            status = _case_status(base_case, candidate_case)
            counts[status] += 1
            suite_counts[status] += 1

            base_score = (
                _number(base_case.get("score")) if base_case is not None else None
            )
            candidate_score = (
                _number(candidate_case.get("score"))
                if candidate_case is not None
                else None
            )
            score_delta = (
                round(candidate_score - base_score, 4)
                if base_score is not None and candidate_score is not None
                else 0.0
            )
            tokens_delta = (
                _integer(candidate_case.get("tokens"))
                - _integer(base_case.get("tokens"))
                if base_case is not None and candidate_case is not None
                else 0
            )
            duration_delta = (
                round(
                    _number(candidate_case.get("duration_ms"))
                    - _number(base_case.get("duration_ms")),
                    2,
                )
                if base_case is not None and candidate_case is not None
                else 0.0
            )
            current = candidate_case if candidate_case is not None else base_case
            case_rows.append(
                EvalCompareCaseRead(
                    suite=name,
                    case_id=case_id,
                    status=status,
                    base_ok=bool(base_case.get("ok")) if base_case else None,
                    candidate_ok=(
                        bool(candidate_case.get("ok")) if candidate_case else None
                    ),
                    base_score=base_score,
                    candidate_score=candidate_score,
                    score_delta=score_delta,
                    tokens_delta=tokens_delta,
                    duration_delta_ms=duration_delta,
                    base_error=(
                        _text(base_case.get("error")) or None
                        if base_case is not None
                        else None
                    ),
                    candidate_error=(
                        _text(candidate_case.get("error")) or None
                        if candidate_case is not None
                        else None
                    ),
                    failures=_case_failures(current),
                    tags=_string_list((current or {}).get("tags")),
                )
            )

        suite_rows.append(
            EvalCompareSuiteRead(
                name=name,
                base_totals=(
                    EvalTotalsRead.model_validate(_totals(list(base_cases.values())))
                    if base_suite is not None
                    else None
                ),
                candidate_totals=(
                    EvalTotalsRead.model_validate(
                        _totals(list(candidate_cases.values()))
                    )
                    if candidate_suite is not None
                    else None
                ),
                regressed=suite_counts["regressed"],
                fixed=suite_counts["fixed"],
                added=suite_counts["added"],
                removed=suite_counts["removed"],
                unchanged=suite_counts["unchanged"],
            )
        )

    base_totals = EvalTotalsRead.model_validate(json_object(base_run.totals_json))
    candidate_totals = EvalTotalsRead.model_validate(
        json_object(candidate_run.totals_json)
    )
    return EvalCompareRead(
        base=run_to_read(base_run),
        candidate=run_to_read(candidate_run),
        totals=EvalCompareTotalsRead(
            regressed=counts["regressed"],
            fixed=counts["fixed"],
            added=counts["added"],
            removed=counts["removed"],
            unchanged=counts["unchanged"],
            base_pass_rate=base_totals.pass_rate,
            candidate_pass_rate=candidate_totals.pass_rate,
            pass_rate_delta=round(
                candidate_totals.pass_rate - base_totals.pass_rate, 4
            ),
            tokens_delta=candidate_totals.tokens - base_totals.tokens,
            duration_delta_ms=round(
                candidate_totals.duration_ms - base_totals.duration_ms, 2
            ),
        ),
        suites=suite_rows,
        cases=case_rows,
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


async def load_report(
    session: AsyncSession, run_id: str, *, store: ArtifactStore | None = None
) -> dict[str, Any]:
    report, _ = await load_report_download(session, run_id, store=store)
    return report


async def load_report_download(
    session: AsyncSession, run_id: str, *, store: ArtifactStore | None = None
) -> tuple[dict[str, Any], EvalRun]:
    """下载用:报告快照 + 运行行(取 report_key/report_size 供审计)。"""

    run = await EvalRunRepository(session).require(run_id)
    return await _report_payload(run, store), run


def report_bytes(report: dict[str, Any]) -> bytes:
    """报告下载的稳定字节序列；与入库序列化保持一致。"""

    return _dump(report).encode("utf-8")


async def load_run_summary(session: AsyncSession, run_id: str) -> EvalRunRead:
    """运行摘要:元数据 + 套件汇总，不含用例明细。"""

    return run_to_read(await EvalRunRepository(session).require(run_id))


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


async def load_case_detail(
    session: AsyncSession, run_id: str, suite: str, case_id: str
) -> EvalCaseRead:
    """单个用例详情；case_id 含 ``/``，因此走查询参数而不是路径参数。"""

    await EvalRunRepository(session).require(run_id)
    await EvalSuiteRunRepository(session).get_for_run(run_id, suite) or _missing_suite(
        suite
    )
    row = await EvalCaseRunRepository(session).get_for_run(run_id, suite, case_id)
    if row is None:
        raise CaseNotFoundError(
            "eval.case_not_found",
            f"用例 {case_id} 不存在",
            details={"suite": suite, "case_id": case_id},
        )
    return _case_to_read(row)


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
    return [baseline_to_read(record) for record in records]


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


def baseline_to_read(record: EvalBaseline) -> EvalBaselineRead:
    """基线元数据 + 用例统计；统计从 payload 现算，避免多一份会漂移的列。"""

    case_count, passed = _baseline_counts(record.payload_json)
    return EvalBaselineRead(
        suite=record.suite,
        dataset_version=record.dataset_version,
        evaluator_version=record.evaluator_version,
        git_sha=record.git_sha,
        approved_by=record.approved_by,
        approved_at=record.approved_at,
        case_count=case_count,
        passed=passed,
    )


def _baseline_counts(payload_json: str) -> tuple[int, int]:
    try:
        payload = json.loads(payload_json)
    except ValueError:  # pragma: no cover - 只有写坏的行才会触发
        return 0, 0
    suites = payload.get("suites") if isinstance(payload, dict) else None
    if not isinstance(suites, list):
        return 0, 0
    cases: list[dict[str, Any]] = []
    for suite in suites:
        if not isinstance(suite, dict):
            continue
        raw = suite.get("cases")
        if isinstance(raw, dict):
            cases.extend(item for item in raw.values() if isinstance(item, dict))
        elif isinstance(raw, list):
            cases.extend(item for item in raw if isinstance(item, dict))
    return len(cases), sum(1 for case in cases if bool(case.get("ok")))
