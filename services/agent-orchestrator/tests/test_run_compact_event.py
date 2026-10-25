"""run_lifecycle.create_run:压缩结果落 run.compacted.v1 事件(纯审计,不进 outbox)。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import select

from agent_orchestrator.models.runs import RunEvent
from agent_orchestrator.schemas.runs import RunCreate
from agent_orchestrator.services import run_lifecycle
from agent_orchestrator.services.compaction import CompactionResult

COVERED_UNTIL = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


async def _create(tmp_path: Path, *, compaction_result: CompactionResult | None):
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/run.db")
    try:
        async with factory.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_scope(factory) as session:
            run = await run_lifecycle.create_run(
                session,
                "t-1",
                RunCreate(title="标题", goal="目标"),
                compaction=compaction_result,
            )
            run_id = run.id
        async with session_scope(factory) as session:
            events = list(
                await session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id)
                    .order_by(RunEvent.seq)
                )
            )
        return events
    finally:
        await factory.engine.dispose()


async def test_create_run_with_compaction_emits_compacted_event(tmp_path: Path) -> None:
    events = await _create(
        tmp_path,
        compaction_result=CompactionResult(
            summary="摘要文本",
            covered_until=COVERED_UNTIL,
            covered_messages=3,
            tokens_before=100,
            tokens_after=20,
        ),
    )

    assert [event.type for event in events] == ["run.created.v1", "run.compacted.v1"]
    assert events[1].seq == 2
    assert events[1].payload == {
        "summary_chars": 4,
        "covered_messages": 3,
        "tokens_before": 100,
        "tokens_after": 20,
    }


async def test_create_run_without_compaction_has_no_compacted_event(
    tmp_path: Path,
) -> None:
    events = await _create(tmp_path, compaction_result=None)

    assert [event.type for event in events] == ["run.created.v1"]
