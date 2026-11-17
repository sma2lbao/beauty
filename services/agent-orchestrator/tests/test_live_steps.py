"""步骤实时落库(S4):节点一结束就写 run.step.v1,而不是等整段图跑完。

两段覆盖:
- 落库时机:图还没退出 `_step_stream` 时,另一个连接已能读到该步骤(证明提交
  发生在节点结束,而不是 ainvoke 边界);
- 包装层行为:成功/失败路径各 flush 一次,失败 span 带 failed 状态且异常照常
  上抛(观测面不影响节点语义);无 sink 时 flush 是 no-op。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.graph.steps import StepProjector, StepSpan, instrument_node
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services.run_events import _step_stream
from agent_orchestrator.services.run_steps import list_steps
from tests.conftest import _settings

ACCOUNT = "t-1"


@pytest.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose,避免句柄残留。"""
    settings = _settings(tmp_path, "live-steps.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


async def _add_run(factory: async_sessionmaker[AsyncSession]) -> str:
    async with session_scope(factory) as session:
        run = Run(account_id=ACCOUNT, title="实时步骤", goal="目标", status="running")
        session.add(run)
        await session.flush()
        return run.id


def test_step_is_visible_before_graph_boundary(factory) -> None:
    """flush 后立刻可读:另一个连接在 `_step_stream` 退出前就看到这一步。"""

    async def scenario() -> None:
        run_id = await _add_run(factory)
        async with session_scope(factory) as session:
            run = await session.get(Run, run_id)
            assert run is not None
            async with _step_stream(session, run) as projector:
                span = projector.begin("plan", "plan")
                projector.finish(span, "completed")
                await projector.flush()
                # 这里等价于"图还在跑":上下文没退出,调用方也还没兜底落库
                async with session_scope(factory) as other:
                    visible = await list_steps(other, ACCOUNT, run_id)
                assert [step.node for step in visible] == ["plan"]
                assert visible[0].status == "completed"

    asyncio.run(scenario())


class _CountingSink:
    """记录 push/flush 次数的 sink(替代 services 层的落库实现)。"""

    def __init__(self) -> None:
        self.spans: list[StepSpan] = []
        self.flushes = 0

    def push(self, span: StepSpan) -> None:
        self.spans.append(span)

    async def flush(self) -> None:
        self.flushes += 1


def _config(sink: _CountingSink) -> dict:
    projector = StepProjector(sink, run_id="r1", attempt=1)
    return {"configurable": {GRAPH_RUNTIME_KEY: GraphRuntime(steps=projector)}}


def test_flush_runs_when_node_completes() -> None:
    sink = _CountingSink()

    async def node(state, config=None):  # noqa: ANN001, ANN202 - 测试桩
        return {"ok": True}

    async def scenario():
        wrapper = instrument_node("plan", "plan", node)
        return await wrapper({"run_id": "r1"}, _config(sink))

    assert asyncio.run(scenario()) == {"ok": True}
    assert sink.flushes == 1
    assert [span.status for span in sink.spans] == ["completed"]


def test_flush_runs_when_node_fails() -> None:
    sink = _CountingSink()

    async def node(state, config=None):  # noqa: ANN001, ANN202 - 测试桩
        raise RuntimeError("boom")

    async def scenario() -> None:
        wrapper = instrument_node("execute", "execute", node)
        with pytest.raises(RuntimeError):
            await wrapper({"run_id": "r1"}, _config(sink))

    asyncio.run(scenario())
    assert sink.flushes == 1
    assert [span.status for span in sink.spans] == ["failed"]


def test_flush_is_noop_without_sink() -> None:
    """直跑节点的单测没有 sink:flush 直接返回,不抛。"""
    asyncio.run(StepProjector().flush())
