"""事务性 outbox 接入(§6.3/§8):同事务记录、后台投递、交付物自动入库。"""

from __future__ import annotations

from pathlib import Path

import pytest
from database.session import create_session_factory, session_scope
from events.envelope import EventEnvelope
from events.outbox import DISPATCHED, OutboxEvent, ProcessedEvent
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_orchestrator.main import create_app
from agent_orchestrator.services.outbox import KnowledgeIngestConsumer, record_run_event
from tests.conftest import (
    TENANT,
    FakeKnowledge,
    FakeLLM,
    RecordingBus,
    _create_run,
    _settings,
)

VERIFY_PASS = '{"summary": "验收通过", "verdicts": {"邮件已汇总": true}}'
DELIVERY = "# 周报(成品)\n\n- 邮件已汇总\n"


def _complete_run(client: TestClient) -> str:
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    response = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    assert response.json()["status"] == "completed"
    return run_id


def _outbox_client(
    tmp_path: Path, bus: RecordingBus | None = None, **overrides
) -> TestClient:
    """outbox 开启、投递间隔拉长(测试里手动 drain,保证确定性)。"""
    settings = _settings(
        tmp_path,
        "outbox.db",
        llm_enabled=True,
        outbox_enabled=True,
        outbox_dispatch_interval_seconds=60.0,
        **overrides,
    )
    return TestClient(
        create_app(
            settings,
            llm_client=FakeLLM(["# 计划", "执行", VERIFY_PASS, DELIVERY]),
            event_bus=bus or RecordingBus(),
        )
    )


async def _outbox_rows(settings, run_id: str | None = None) -> list:
    """读 outbox 行(按 run_id 过滤,便于只看本次 run 的事件)。"""
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            rows = list(await session.scalars(select(OutboxEvent)))
        return [
            row for row in rows if run_id is None or row.payload.get("run_id") == run_id
        ]
    finally:
        await factory.engine.dispose()


def test_outbox_disabled_by_default(tmp_path: Path) -> None:
    """默认关闭:不产生 outbox 行(既有部署零影响)。"""
    app = create_app(
        _settings(tmp_path, "off.db", llm_enabled=True),
        llm_client=FakeLLM(["# 计划", "执行", VERIFY_PASS, DELIVERY]),
    )
    with TestClient(app) as client:
        run_id = _complete_run(client)
        assert app.state.outbox_recorder is None
        assert app.state.outbox_dispatcher is None

    rows = _run_sync_outbox_rows(_settings(tmp_path, "off.db"), run_id)
    assert rows == []


def _run_sync_outbox_rows(settings, run_id: str) -> list:
    import asyncio

    return asyncio.run(_outbox_rows(settings, run_id))


def test_run_events_land_in_outbox_and_dispatch(tmp_path: Path) -> None:
    bus = RecordingBus()
    with _outbox_client(tmp_path, bus) as client:
        run_id = _complete_run(client)
        settings = client.app.state.settings

        rows = _run_sync_outbox_rows(settings, run_id)
        types = [row.event_type for row in rows]
        assert "run.created.v1" in types
        assert "run.delivered.v1" in types
        assert all(row.payload.get("run_id") == run_id for row in rows)
        assert all(row.status == "pending" for row in rows)

        stats = _run_sync_drain(client.app.state.outbox_dispatcher)
        assert stats.claimed == len(rows)
        assert stats.failed == 0

        # 事件自洽:租户随信封、run_id 在 payload 里
        delivered = next(e for e in bus.received if e.type == "run.delivered.v1")
        assert delivered.tenant_id == "t-1"
        assert delivered.payload["run_id"] == run_id
        assert "run.completed.v1" in bus.types

        after = _run_sync_outbox_rows(settings, run_id)
        assert all(row.status == DISPATCHED for row in after)


def _run_sync_drain(dispatcher):
    import asyncio

    return asyncio.run(dispatcher.drain_once())


async def test_append_event_rolls_back_outbox_row(tmp_path: Path) -> None:
    """核心性质:业务事务回滚,发件箱里的行也一起回滚。"""
    settings = _settings(tmp_path, "tx.db", llm_enabled=True)
    factory = create_session_factory(settings.database_url)
    try:
        async with factory.engine.begin() as conn:
            from database.base import Base

            await conn.run_sync(Base.metadata.create_all)

        class Boom(RuntimeError):
            pass

        from agent_orchestrator.models.runs import Run

        with pytest.raises(Boom):
            async with session_scope(factory) as session:
                run = Run(tenant_id="t-1", title="滚回", goal="g", status="pending")
                session.add(run)
                await session.flush()
                await record_run_event(
                    session,
                    run,
                    EventEnvelope.create("run.created.v1", {"title": "滚回"}),
                )
                raise Boom("业务失败")

        async with session_scope(factory) as session:
            rows = list(await session.scalars(select(OutboxEvent)))
            assert rows == []
    finally:
        await factory.engine.dispose()


def test_knowledge_ingest_consumer_ingests_delivery(tmp_path: Path) -> None:
    """消费者:run.delivered.v1 → 知识库入库;重复投递不重复入库。"""
    import asyncio

    settings = _settings(tmp_path, "ingest.db", llm_enabled=True)
    app = create_app(
        settings,
        llm_client=FakeLLM(["# 计划", "执行", VERIFY_PASS, DELIVERY]),
    )
    knowledge = FakeKnowledge()
    with TestClient(app) as client:
        run_id = _complete_run(client)
        factory = create_session_factory(settings.database_url)
        consumer = KnowledgeIngestConsumer(knowledge, factory, None)
        envelope = EventEnvelope(
            id="e1",
            type="run.delivered.v1",
            occurred_at="2026-10-13T00:00:00+00:00",
            payload={"run_id": run_id},
            tenant_id="t-1",
        )
        asyncio.run(consumer(envelope))

        assert len(knowledge.ingested) == 1
        assert knowledge.ingested[0]["title"] == "周报"
        assert knowledge.ingested[0]["content"] == DELIVERY
        assert knowledge.ingested[0]["source"] == f"run:{run_id}"
        assert knowledge.ingested[0]["tenant_id"] == "t-1"

        # 重投(至少一次语义):事件级去重 + 内容级去重都不该重复入库
        asyncio.run(consumer(envelope))
        assert len(knowledge.ingested) == 1

        async def _processed_count() -> int:
            async with session_scope(factory) as session:
                rows = list(
                    await session.scalars(
                        select(ProcessedEvent).where(ProcessedEvent.event_id == "e1")
                    )
                )
                return len(rows)

        assert asyncio.run(_processed_count()) == 1
        asyncio.run(factory.engine.dispose())


def test_knowledge_ingest_failure_propagates_for_retry(tmp_path: Path) -> None:
    """知识库不可用:消费者抛错 → dispatcher 记失败并退避重试(不静默丢事件)。"""
    import asyncio

    settings = _settings(tmp_path, "ingest-fail.db", llm_enabled=True)
    app = create_app(
        settings,
        llm_client=FakeLLM(["# 计划", "执行", VERIFY_PASS, DELIVERY]),
    )
    knowledge = FakeKnowledge(fail=True)
    with TestClient(app) as client:
        run_id = _complete_run(client)
        factory = create_session_factory(settings.database_url)
        consumer = KnowledgeIngestConsumer(knowledge, factory, None)
        envelope = EventEnvelope(
            id="e2",
            type="run.delivered.v1",
            occurred_at="2026-10-13T00:00:00+00:00",
            payload={"run_id": run_id},
            tenant_id="t-1",
        )
        with pytest.raises(RuntimeError):
            asyncio.run(consumer(envelope))
        assert knowledge.ingested == []
        asyncio.run(factory.engine.dispose())


def test_consumer_ignores_other_event_types(tmp_path: Path) -> None:
    import asyncio

    settings = _settings(tmp_path, "ingest-skip.db", llm_enabled=True)
    factory = create_session_factory(settings.database_url)
    knowledge = FakeKnowledge()
    consumer = KnowledgeIngestConsumer(knowledge, factory, None)

    asyncio.run(
        consumer(
            EventEnvelope(
                id="e3",
                type="run.approved.v1",
                occurred_at="2026-10-13T00:00:00+00:00",
                payload={},
                tenant_id="t-1",
            )
        )
    )
    assert knowledge.ingested == []
    asyncio.run(factory.engine.dispose())
