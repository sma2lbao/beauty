"""services.context_service:上下文装配的服务层边界(P1-F §7.4)。

覆盖点:
- 正常:管线结果折叠成 RunContext(block/snapshot/tokens/section_ids),
  block 带段标题、快照不含正文;
- 关闭/异常/空块:一律返回 None,让执行图退回 legacy 的 skill/memory 注入;
- 事件:run.context.assembled.v1 带策略快照 + attempt/tokens_estimate/
  section_ids;事件写失败只降级、不抛。

零网络、零模型:管线用固定/故障 provider,库用临时 sqlite。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.context.pipeline import (
    ContextPipeline,
    ContextRequest,
    ContextSection,
)
from agent_orchestrator.context.policy import SECTION_MEMORY, ContextPolicy
from agent_orchestrator.models.runs import Run, RunEvent
from agent_orchestrator.services import context_service


@asynccontextmanager
async def _session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    """独立临时库:建表走 create_all(与既有 service 测试一致)。"""

    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/context.db")
    try:
        async with factory.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_scope(factory) as db_session:
            yield db_session
    finally:
        await factory.engine.dispose()


def _run(**overrides: Any) -> Run:
    values: dict[str, Any] = {
        "tenant_id": "t-1",
        "title": "整理本周日程",
        "goal": "把本周日程整理成一份清单",
        "workspace_id": "ws-1",
        "conversation_id": "c-1",
    }
    values.update(overrides)
    return Run(**values)


class _FixedProvider:
    """固定产出一段上下文,并记下收到的请求。"""

    section_id = SECTION_MEMORY

    def __init__(self, content: str = "用户偏好中文回复") -> None:
        self.content = content
        self.requests: list[ContextRequest] = []

    async def build(self, request: ContextRequest) -> ContextSection | None:
        self.requests.append(request)
        return ContextSection(
            id=self.section_id,
            title="长期记忆(相关召回)",
            content=self.content,
            source="memory",
        )


class _BrokenProvider:
    """IO 失败:管线降级为 skipped,服务层折叠成 None。"""

    section_id = SECTION_MEMORY

    async def build(self, request: ContextRequest) -> ContextSection | None:
        raise RuntimeError("记忆检索挂了")


async def test_build_run_context_injects_sections(tmp_path: Path) -> None:
    run = _run()
    provider = _FixedProvider()
    pipeline = ContextPipeline(ContextPolicy(), providers=[provider])
    async with _session(tmp_path) as session:
        session.add(run)
        await session.flush()
        context = await context_service.build_run_context(
            session, run, pipeline=pipeline, memory_block="用户偏好中文回复"
        )
        await session.flush()

    assert context is not None
    assert context.injected is True
    # 渲染带段标题;快照只含段摘要(不含正文,避免事件体积爆炸)
    assert context.block == "\n\n## 长期记忆(相关召回)\n\n用户偏好中文回复"
    assert context.section_ids == (SECTION_MEMORY,)
    assert context.tokens_estimate == len("用户偏好中文回复") // 2
    assert context.snapshot["sections"] == [
        {
            "id": SECTION_MEMORY,
            "title": "长期记忆(相关召回)",
            "chars": len("用户偏好中文回复"),
            "source": "memory",
            "truncated": False,
        }
    ]
    # 请求是 run 行的快照值(管线不认识 ORM)
    request = provider.requests[0]
    assert request.run_id == run.id
    assert request.tenant_id == "t-1"
    assert request.workspace_id == "ws-1"
    assert request.goal == "把本周日程整理成一份清单"
    assert request.memory_block == "用户偏好中文回复"


async def test_build_run_context_returns_none_when_policy_disabled(
    tmp_path: Path,
) -> None:
    provider = _FixedProvider()
    pipeline = ContextPipeline(ContextPolicy(enabled=False), providers=[provider])
    run = _run()
    async with _session(tmp_path) as session:
        session.add(run)
        await session.flush()
        context = await context_service.build_run_context(
            session, run, pipeline=pipeline
        )

    assert context is None
    assert provider.requests == []


async def test_build_run_context_returns_none_when_provider_fails(
    tmp_path: Path,
) -> None:
    pipeline = ContextPipeline(ContextPolicy(), providers=[_BrokenProvider()])
    run = _run()
    async with _session(tmp_path) as session:
        session.add(run)
        await session.flush()
        context = await context_service.build_run_context(
            session, run, pipeline=pipeline
        )

    assert context is None


async def test_build_run_context_returns_none_when_block_empty(tmp_path: Path) -> None:
    """段内容为空等同未注入:返回 None,执行图走 legacy 路径。"""

    pipeline = ContextPipeline(
        ContextPolicy(), providers=[_FixedProvider(content="  ")]
    )
    run = _run()
    async with _session(tmp_path) as session:
        session.add(run)
        await session.flush()
        context = await context_service.build_run_context(
            session, run, pipeline=pipeline
        )

    assert context is None


async def test_append_context_event_records_policy_snapshot(tmp_path: Path) -> None:
    pipeline = ContextPipeline(ContextPolicy(), providers=[_FixedProvider()])
    run = _run()
    async with _session(tmp_path) as session:
        session.add(run)
        await session.flush()
        context = await context_service.build_run_context(
            session, run, pipeline=pipeline
        )
        assert context is not None
        await context_service.append_context_event(session, run, context)
        rows = (
            await session.scalars(select(RunEvent).where(RunEvent.run_id == run.id))
        ).all()

    assert [row.type for row in rows] == [context_service.CONTEXT_ASSEMBLED_EVENT]
    payload = rows[0].payload
    assert payload["attempt"] == 1
    assert payload["tokens_estimate"] == context.tokens_estimate
    assert payload["section_ids"] == [SECTION_MEMORY]
    assert payload["fingerprint"] == pipeline.policy.fingerprint()
    assert payload["policy"]["enabled"] is True


async def test_append_context_event_swallows_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """事件是观测面:写失败不能让 run 停在 planning。"""

    async def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("事件库挂了")

    monkeypatch.setattr(context_service, "append_event", _boom)
    run = _run()
    context = context_service.RunContext(
        block="x", snapshot={"fingerprint": "abc"}, tokens_estimate=1, section_ids=()
    )
    async with _session(tmp_path) as session:
        session.add(run)
        await session.flush()
        await context_service.append_context_event(session, run, context)
