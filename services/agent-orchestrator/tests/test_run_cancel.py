"""run 取消(P0-2):状态机、事件、job 清理与租户隔离。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from database.session import session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.repositories import JobRepository
from agent_orchestrator.schemas.runs import RunCreate
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import runs as runs_service
from tests.conftest import _IDP, OTHER, TENANT, _create_run, _settings


def _event_types(client: TestClient, run_id: str) -> list[str]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT)
    assert resp.status_code == 200
    return [event["type"] for event in resp.json()]


def test_cancel_pending_run_records_reason_and_event(client: TestClient) -> None:
    run_id = _create_run(client)

    resp = client.post(
        f"/api/v1/runs/{run_id}/cancel",
        json={"reason": "需求取消"},
        headers=TENANT,
    )
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "cancelled"
    assert run["status_reason"] == "需求取消"

    events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
    assert [event["type"] for event in events] == [
        "run.created.v1",
        "run.cancelled.v1",
    ]
    assert events[-1]["payload"]["reason"] == "需求取消"
    assert events[-1]["payload"]["cancelled_jobs"] == 0


def test_cancel_without_body_uses_default_reason(client: TestClient) -> None:
    run_id = _create_run(client)

    resp = client.post(f"/api/v1/runs/{run_id}/cancel", headers=TENANT)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["status_reason"] == "已取消"


def test_cancel_awaiting_approval_then_actions_are_rejected(
    client: TestClient,
) -> None:
    run_id = _create_run(client)
    assert client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).status_code == 200

    resp = client.post(
        f"/api/v1/runs/{run_id}/cancel",
        json={"reason": "计划不做了"},
        headers=TENANT,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert "run.cancelled.v1" in _event_types(client, run_id)

    # 终态之后:审批/拒绝/恢复/追加指令/再次取消全部 409
    for path, payload in (
        ("approve", None),
        ("reject", {"reason": "晚了"}),
        ("resume", None),
        ("cancel", {"reason": "再取消"}),
    ):
        resp = client.post(
            f"/api/v1/runs/{run_id}/{path}", json=payload, headers=TENANT
        )
        assert resp.status_code == 409, path
        assert resp.json()["code"] == "run.invalid_state", path

    resp = client.post(
        f"/api/v1/runs/{run_id}/instructions",
        json={"content": "还有新指令"},
        headers=TENANT,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "run.invalid_state"


def test_cancel_blocked_run(client: TestClient) -> None:
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    blocked = client.post(
        f"/api/v1/runs/{run_id}/reject",
        json={"reason": "方向不对"},
        headers=TENANT,
    ).json()
    assert blocked["status"] == "blocked"

    resp = client.post(f"/api/v1/runs/{run_id}/cancel", headers=TENANT)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_cancel_completed_run_is_conflict(client: TestClient) -> None:
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    assert (
        client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()["status"]
        == "completed"
    )

    resp = client.post(f"/api/v1/runs/{run_id}/cancel", headers=TENANT)
    assert resp.status_code == 409
    assert resp.json()["code"] == "run.invalid_state"


def test_cancel_is_tenant_scoped_and_unknown_run_is_404(client: TestClient) -> None:
    run_id = _create_run(client)

    resp = client.post(f"/api/v1/runs/{run_id}/cancel", headers=OTHER)
    assert resp.status_code == 404
    assert resp.json()["code"] == "run.not_found"
    # 跨租户取消不能真的改动状态
    assert (
        client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()["status"]
        == "pending"
    )

    resp = client.post("/api/v1/runs/nonexistent/cancel", headers=TENANT)
    assert resp.status_code == 404
    assert resp.json()["code"] == "run.not_found"


def test_cancel_marks_queued_jobs_cancelled(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path, "cancel-jobs.db", jobs_enabled=False),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app):
        pass

    async def scenario() -> None:
        factory = app.state.session_factory
        async with session_scope(factory) as session:
            run = await runs_service.create_run(
                session,
                "t-1",
                RunCreate(title="排队取消", goal="跑一下", acceptance_criteria=[]),
            )
            job = await jobs_service.enqueue(session, "t-1", "plan", run.id)
            cancelled = await runs_service.cancel_run(session, "t-1", run.id, "不要了")
            assert cancelled.status == "cancelled"
            assert cancelled.status_reason == "不要了"
        async with session_scope(factory) as session:
            stored = await JobRepository(session).get_for_tenant("t-1", job.id)
            assert stored is not None
            assert stored.status == "cancelled"
            assert stored.lease_until is None
        await factory.engine.dispose()

    asyncio.run(scenario())
