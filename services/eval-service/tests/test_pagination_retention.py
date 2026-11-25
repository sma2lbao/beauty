"""分页/对象存储/保留期:服务层直测 + 端点新路径。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from common.artifacts import FilesystemArtifactStore
from common.errors import AppError
from database.base import Base
from database.session import create_session_factory
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from eval_service.maintenance import main as maintenance_main
from eval_service.models import EvalCaseRun, EvalRun
from eval_service.schemas.evals import EvalRunUpload
from eval_service.services.evals import (
    compare_runs,
    ingest_run,
    load_case_detail,
    load_report,
    load_run_summary,
)
from eval_service.services.retention import purge_expired
from tests.conftest import AUTH, _settings
from tests.test_evals import case, report, suite, upload


@pytest.fixture(name="session")
async def session_fixture(tmp_path: Any):
    factory = create_session_factory(_settings(tmp_path).database_url)
    async with factory() as session:
        connection = await session.connection()
        await connection.run_sync(Base.metadata.create_all)
        yield session


def _body(external_id: str, *, text: str = "x" * 500) -> EvalRunUpload:
    item = case("chat/a")
    item["output"] = text
    return EvalRunUpload(
        external_id=external_id,
        report=report(suite("agent-core", [item])),
    )


async def test_large_report_offloads_and_reads_back(
    session: Any, tmp_path: Any
) -> None:
    store = FilesystemArtifactStore(tmp_path / "objects")
    run = await ingest_run(session, _body("big-1"), store=store, inline_limit=64)
    await session.commit()

    assert run.report_key
    assert run.report_size > 64
    summary = json.loads(run.report_json)
    assert "cases" not in summary["suites"][0]
    assert summary["suites"][0]["case_count"] == 1
    assert await store.get(run.report_key) is not None

    detail = await load_report(session, str(run.id), store=store)
    assert detail["suites"][0]["cases"][0]["output"] == "x" * 500

    with pytest.raises(AppError) as unavailable:
        await load_report(session, str(run.id))
    assert unavailable.value.status == 503
    assert unavailable.value.code == "eval.report_store_unavailable"

    await store.delete(run.report_key)
    with pytest.raises(AppError) as missing:
        await load_report(session, str(run.id), store=store)
    assert missing.value.status == 410
    assert missing.value.code == "eval.report_missing"


async def test_compare_reads_offloaded_reports(session: Any, tmp_path: Any) -> None:
    store = FilesystemArtifactStore(tmp_path / "objects")
    base = await ingest_run(
        session, _body("cmp-big-base"), store=store, inline_limit=64
    )
    candidate = await ingest_run(
        session, _body("cmp-big-candidate"), store=store, inline_limit=64
    )
    await session.commit()

    result = await compare_runs(session, str(base.id), str(candidate.id), store=store)
    assert result.totals.unchanged == 1
    assert result.totals.regressed == 0

    with pytest.raises(AppError) as unavailable:
        await compare_runs(session, str(base.id), str(candidate.id))
    assert unavailable.value.status == 503


async def test_summary_and_case_detail(session: Any) -> None:
    run = await ingest_run(session, _body("detail-1"))
    await session.commit()

    summary = await load_run_summary(session, str(run.id))
    assert summary.id == str(run.id)
    assert summary.suites[0].name == "agent-core"
    assert summary.totals.cases == 1

    detail = await load_case_detail(session, str(run.id), "agent-core", "chat/a")
    assert detail.id == "chat/a"
    assert detail.ok is True

    with pytest.raises(AppError) as missing_case:
        await load_case_detail(session, str(run.id), "agent-core", "chat/nope")
    assert missing_case.value.status == 404
    assert missing_case.value.code == "eval.case_not_found"

    with pytest.raises(AppError) as missing_suite:
        await load_case_detail(session, str(run.id), "nope", "chat/a")
    assert missing_suite.value.code == "eval.suite_not_found"


def test_summary_and_case_endpoints(client: TestClient) -> None:
    run = upload(
        client,
        {
            "external_id": "api-detail",
            "report": report(suite("agent-core", [case("chat/a")])),
        },
    ).json()
    summary = client.get(f"/v1/runs/{run['id']}/summary", headers=AUTH)
    assert summary.status_code == 200, summary.text
    assert summary.json()["suites"][0]["name"] == "agent-core"

    detail = client.get(
        f"/v1/runs/{run['id']}/case",
        params={"suite": "agent-core", "case_id": "chat/a"},
        headers=AUTH,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == "chat/a"

    missing = client.get(
        f"/v1/runs/{run['id']}/case",
        params={"suite": "agent-core", "case_id": "chat/missing"},
        headers=AUTH,
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "eval.case_not_found"


async def test_purge_expired_removes_rows_and_objects(
    session: Any, tmp_path: Any
) -> None:
    store = FilesystemArtifactStore(tmp_path / "objects")
    old = await ingest_run(session, _body("old-1"), store=store, inline_limit=64)
    keep = await ingest_run(session, _body("new-1", text="fresh"))
    await session.commit()
    assert old.report_key

    old_created = datetime.now(UTC) - timedelta(days=40)
    await session.execute(
        update(EvalRun).where(EvalRun.id == old.id).values(created_at=old_created)
    )
    await session.commit()
    before = datetime.now(UTC) - timedelta(days=30)

    assert await purge_expired(session, store, before=before, dry_run=True) == 1
    removed = await purge_expired(session, store, before=before)
    await session.commit()
    assert removed == 1
    assert await store.get(old.report_key) is None
    assert await session.scalar(select(EvalRun).where(EvalRun.id == old.id)) is None
    assert (
        await session.scalar(select(EvalCaseRun).where(EvalCaseRun.run_id == old.id))
        is None
    )
    assert (
        await session.scalar(select(EvalRun).where(EvalRun.id == keep.id)) is not None
    )


async def test_purge_requires_store_for_offloaded_reports(
    session: Any, tmp_path: Any
) -> None:
    store = FilesystemArtifactStore(tmp_path / "objects")
    run = await ingest_run(session, _body("old-2"), store=store, inline_limit=64)
    await session.commit()
    await session.execute(
        update(EvalRun)
        .where(EvalRun.id == run.id)
        .values(created_at=datetime.now(UTC) - timedelta(days=40))
    )
    await session.commit()

    with pytest.raises(AppError) as unavailable:
        await purge_expired(
            session, None, before=datetime.now(UTC) - timedelta(days=30)
        )
    assert unavailable.value.status == 503


def test_maintenance_disabled_retention_skips() -> None:
    assert maintenance_main(["purge", "--days", "0"]) == 0
