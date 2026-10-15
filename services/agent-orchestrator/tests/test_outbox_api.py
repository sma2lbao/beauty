"""outbox 运维端点:列表/计数/人工重放、租户隔离、未启用时明确报错。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from database.session import create_session_factory, session_scope
from events.envelope import EventEnvelope
from events.outbox import FAILED, PENDING, OutboxRepository
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import OTHER, TENANT, RecordingBus, _settings, verifier
from tests.test_outbox import _outbox_client


async def _record_failed(settings, tenant_id: str, event_id: str) -> None:
    """直接落一条 failed 事件(模拟消费者耗尽重试)。"""
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            repository = OutboxRepository(session)
            envelope = EventEnvelope(
                id=event_id,
                type="run.delivered.v1",
                occurred_at="2026-10-13T00:00:00+00:00",
                payload={"run_id": "run-1"},
                tenant_id=tenant_id,
            )
            event = await repository.record(tenant_id, envelope)
            await repository.mark_failed(event, "消费者挂了", max_attempts=1)
    finally:
        await factory.engine.dispose()


def test_endpoints_report_disabled_outbox(tmp_path: Path) -> None:
    """未启用时明确 409,而不是返回空列表让人误读成"没有事件"。"""
    app = create_app(_settings(tmp_path, "off.db"), token_verifier=verifier())
    with TestClient(app) as client:
        for path in ("/api/v1/outbox/events", "/api/v1/outbox/stats"):
            response = client.get(path, headers=TENANT)
            assert response.status_code == 409
            assert response.json()["code"] == "outbox.disabled"
        assert (
            client.post("/api/v1/outbox/retry-failed", headers=TENANT).status_code
            == 409
        )


def test_stats_and_list_are_tenant_scoped(tmp_path: Path) -> None:
    with _outbox_client(tmp_path, RecordingBus()) as client:
        settings = client.app.state.settings
        asyncio.run(_record_failed(settings, "t-1", "e-mine"))
        asyncio.run(_record_failed(settings, "t-2", "e-other"))

        stats = client.get("/api/v1/outbox/stats", headers=TENANT)
        assert stats.status_code == 200
        assert stats.json() == {"pending": 0, "dispatched": 0, "failed": 1}

        events = client.get(
            "/api/v1/outbox/events?status=failed", headers=TENANT
        ).json()
        assert [event["id"] for event in events] == ["e-mine"]
        assert events[0]["last_error"] == "消费者挂了"
        assert events[0]["payload"] == {"run_id": "run-1"}

        # 别租户看不到,也重放不了(404,不泄漏存在性)
        assert (
            client.get("/api/v1/outbox/events", headers=OTHER).json()[0]["id"]
            == "e-other"
        )
        missing = client.post("/api/v1/outbox/events/e-mine/retry", headers=OTHER)
        assert missing.status_code == 404
        assert missing.json()["code"] == "outbox.event_not_found"


def test_retry_single_event_returns_to_pending(tmp_path: Path) -> None:
    with _outbox_client(tmp_path, RecordingBus()) as client:
        settings = client.app.state.settings
        asyncio.run(_record_failed(settings, "t-1", "e-retry"))

        response = client.post("/api/v1/outbox/events/e-retry/retry", headers=TENANT)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == PENDING
        assert body["attempts"] == 0
        assert body["last_error"] == "消费者挂了"  # 重放前仍能看到上次原因

        stats = client.get("/api/v1/outbox/stats", headers=TENANT).json()
        assert stats == {"pending": 1, "dispatched": 0, "failed": 0}

        # 重放后 dispatcher 能正常投递,成功时清掉 last_error
        dispatched = asyncio.run(client.app.state.outbox_dispatcher.drain_once())
        assert (dispatched.claimed, dispatched.delivered) == (1, 1)

        events = client.get(
            "/api/v1/outbox/events?status=dispatched", headers=TENANT
        ).json()
        assert events[0]["last_error"] is None


def test_retry_failed_batch(tmp_path: Path) -> None:
    with _outbox_client(tmp_path, RecordingBus()) as client:
        settings = client.app.state.settings
        asyncio.run(_record_failed(settings, "t-1", "e1"))
        asyncio.run(_record_failed(settings, "t-1", "e2"))
        asyncio.run(_record_failed(settings, "t-2", "e3"))

        response = client.post("/api/v1/outbox/retry-failed", headers=TENANT)
        assert response.json() == {"requeued": 2}
        # 只动了本租户
        assert client.get("/api/v1/outbox/stats", headers=OTHER).json()[FAILED] == 1


def test_invalid_status_filter_rejected(tmp_path: Path) -> None:
    with _outbox_client(tmp_path, RecordingBus()) as client:
        response = client.get("/api/v1/outbox/events?status=nope", headers=TENANT)
        assert response.status_code == 422
