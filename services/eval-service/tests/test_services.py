"""服务层直测:绕过 TestClient 线程,覆盖归一化/漂移/分页/基线逻辑。"""

from __future__ import annotations

from typing import Any

import pytest
from common.errors import AppError
from database.base import Base
from database.session import create_session_factory

from eval_service.schemas.evals import EvalRunUpload
from eval_service.services.evals import (
    approve_baseline,
    get_baseline_payload,
    ingest_run,
    list_baselines,
    load_case_page,
    load_index,
    load_report,
)
from tests.conftest import _settings


@pytest.fixture(name="session")
async def session_fixture(tmp_path: Any):
    factory = create_session_factory(_settings(tmp_path).database_url)
    async with factory() as session:
        connection = await session.connection()
        await connection.run_sync(Base.metadata.create_all)
        yield session


def case(case_id: str, *, ok: bool = True, **extra: Any) -> dict[str, Any]:
    return {"id": case_id, "ok": ok, "score": 1.0 if ok else 0.0, **extra}


def report(*suites: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "generated_at": "", "totals": {}, "suites": list(suites)}


def suite(name: str, cases: list[dict[str, Any]], version: int = 1) -> dict[str, Any]:
    return {
        "name": name,
        "dataset": f"evals/datasets/{name}",
        "dataset_version": version,
        "cases": cases,
    }


def baseline_payload(case_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "suites": [
            {
                "name": "agent-core",
                "dataset": "evals/datasets/agent-core",
                "dataset_version": 1,
                "cases": {
                    case_id: {
                        "ok": True,
                        "score": 1.0,
                        "tokens": 10,
                        "metrics": {},
                        "events": [],
                        "tool_calls": [],
                    }
                },
            }
        ],
    }


async def test_ingest_aggregates_and_annotates_drift(session: Any) -> None:
    body = EvalRunUpload(
        external_id="direct-1",
        report=report(
            suite(
                "agent-core",
                [
                    case("chat/a", ok=False, error="boom", failures=["缺少关键词"]),
                    case("chat/b", tokens=7, duration_ms=3.5),
                ],
            ),
            suite("retrieval", [case("q01")], version=2),
        ),
        baselines={"agent-core": baseline_payload("chat/a")},
    )
    run = await ingest_run(session, body)
    await session.commit()

    assert run.drift_count > 0
    assert run.failed_count == 1
    assert run.dataset_versions_json == '{"agent-core": 1, "retrieval": 2}'
    assert "chat/a" in run.failed_cases_json
    assert "缺少关键词" in run.failed_cases_json

    stored = await load_report(session, str(run.id))
    assert stored["drifts"]
    assert stored["totals"]["cases"] == 3
    assert stored["suites"][0]["drift_count"] > 0
    assert stored["suites"][0]["cases"][0]["baseline_diff"]

    again = await ingest_run(session, body)
    assert again.id == run.id


async def test_load_index_and_case_page(session: Any) -> None:
    first = await ingest_run(
        session,
        EvalRunUpload(
            external_id="idx-1",
            branch="main",
            report=report(
                suite("agent-core", [case("chat/a"), case("chat/b", ok=False)])
            ),
        ),
    )
    await ingest_run(
        session,
        EvalRunUpload(
            external_id="idx-2",
            branch="dev",
            report=report(suite("retrieval", [case("q01")])),
        ),
    )
    await session.commit()

    index = await load_index(session)
    assert len(index.runs) == 2
    assert index.runs[0].id == str((await load_index(session, branch="dev")).runs[0].id)

    failed = await load_index(session, status="failed")
    assert [item.id for item in failed.runs] == [str(first.id)]
    by_suite = await load_index(session, suite="retrieval")
    assert len(by_suite.runs) == 1
    paged = await load_index(session, limit=1, offset=1)
    assert len(paged.runs) == 1

    page = await load_case_page(session, str(first.id), "agent-core", status="failed")
    assert page.total == 1
    assert page.items[0].id == "chat/b"
    assert page.items[0].ok is False
    all_items = await load_case_page(session, str(first.id), "agent-core")
    assert all_items.total == 2

    with pytest.raises(AppError):
        await load_case_page(session, str(first.id), "missing")
    with pytest.raises(AppError):
        await load_report(session, "missing-run")


async def test_baseline_approval_roundtrip(session: Any) -> None:
    payload = baseline_payload("chat/a")
    record = await approve_baseline(
        session,
        "agent-core",
        payload=payload,
        dataset_version=1,
        evaluator_version="1",
        git_sha="c" * 40,
        approved_by="t-1",
    )
    await session.commit()
    assert record.approved_by == "t-1"

    listed = await list_baselines(session)
    assert [item.suite for item in listed] == ["agent-core"]
    assert await get_baseline_payload(session, "agent-core") == payload

    run = await ingest_run(
        session,
        EvalRunUpload(
            external_id="approved-direct",
            report=report(suite("agent-core", [case("chat/a", ok=False)])),
        ),
    )
    assert run.drift_count > 0

    with pytest.raises(AppError):
        await get_baseline_payload(session, "retrieval")


@pytest.mark.parametrize(
    "bad_report",
    [
        {"suites": "nope"},
        {"suites": [1]},
        {"suites": []},
        {"suites": [{"name": "", "cases": []}]},
        {"suites": [{"name": "x"}]},
        {"suites": [{"name": "x", "cases": [1]}]},
        {"suites": [{"name": "x", "cases": [{"ok": True}]}]},
    ],
)
async def test_rejects_malformed_reports(session: Any, bad_report: Any) -> None:
    with pytest.raises(AppError) as caught:
        await ingest_run(session, EvalRunUpload(report=bad_report))
    assert caught.value.status == 400


async def test_baseline_with_wrong_suite_is_ignored(session: Any) -> None:
    run = await ingest_run(
        session,
        EvalRunUpload(
            external_id="wrong-suite",
            report=report(suite("agent-core", [case("chat/a")])),
            baselines={"agent-core": {"version": 1, "suites": [{"name": "other"}]}},
        ),
    )
    assert run.drift_count == 0


async def test_corrupt_baseline_payload_is_reported(session: Any) -> None:
    await approve_baseline(
        session,
        "agent-core",
        payload=baseline_payload("chat/a"),
        dataset_version=1,
        evaluator_version="1",
        git_sha="d" * 40,
        approved_by="t-1",
    )
    await session.commit()
    from sqlalchemy import update

    from eval_service.models import EvalBaseline

    await session.execute(update(EvalBaseline).values(payload_json="{not json"))
    await session.commit()
    with pytest.raises(AppError) as caught:
        await get_baseline_payload(session, "agent-core")
    assert caught.value.code == "eval.report_invalid"
