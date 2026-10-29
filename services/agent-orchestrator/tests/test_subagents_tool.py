"""子代理委派 P1-E 单测:工具参数、上限、继承、完整 spawn、取消/超时与投影。

约定:临时 sqlite 文件库 + 真实 Run 行;子 run 的执行用桩图(只实现
``ainvoke``)——本文件验证的是 spawn 的编排语义(建行/事件/轮询收尾/用量),
真实图行为由既有 run 测试覆盖,零网络。
本文件承载原 ``test_subagents.py`` 拆分后的spawn 工具面(参数/上限/白名单继承)部分。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.container_assemblies import ContainerAssemblyMixin
from agent_orchestrator.graph import items
from agent_orchestrator.models.runs import PARENT_KIND_SUBAGENT, Run, RunEvent
from agent_orchestrator.services import subagents
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import _settings

TENANT = "t-1"
OTHER = "t-2"

PARAMS = {
    "title": "调研竞品",
    "task": "整理三家的定价页差异",
    "agent_name": "调研员",
    "instructions": "只做调研,不改代码",
}


class _StubGraph:
    """桩图:只实现 ``ainvoke``,按用例给终态/中断/挂起。"""

    def __init__(
        self,
        *,
        final_status: str = "completed",
        results: tuple[str, ...] = ("调研完成",),
        status_reason: str | None = None,
        interrupt: bool = False,
        artifact_content: str = "调研完成",
    ) -> None:
        self.final_status = final_status
        self.results = list(results)
        self.status_reason = status_reason
        self.interrupt = interrupt
        self.artifact_content = artifact_content

    async def ainvoke(
        self, state: dict[str, Any], config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.interrupt:
            return {"plan_markdown": "# 计划", "__interrupt__": [{"kind": "plan"}]}
        values: dict[str, Any] = {
            "plan_markdown": "# 计划",
            "rounds": 1,
            "final_status": self.final_status,
            "results": self.results,
            # 真实图在交付阶段必给 artifact:摘要优先取报告正文(§6)
            "artifact": {
                "kind": "markdown",
                "title": "子代理报告",
                "content": self.artifact_content,
            },
        }
        if self.status_reason is not None:
            values["status_reason"] = self.status_reason
        return values


class _HangingGraph:
    """永不返回的桩图:用于超时/取消路径(任务被 cancel 后即结束)。"""

    async def ainvoke(
        self, state: dict[str, Any], config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {}


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose。"""

    settings = _settings(tmp_path, "subagents.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


@pytest.fixture
def settings(tmp_path: Path):
    return _settings(tmp_path, "subagents.db")


async def _create_parent(
    factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str = TENANT,
    parent_kind: str | None = None,
    parent_run_id: str | None = None,
    **kwargs: Any,
) -> Run:
    async with session_scope(factory) as session:
        run = Run(
            tenant_id=tenant_id,
            title="主任务",
            goal="把竞品资料整理成表格",
            parent_kind=parent_kind,
            parent_run_id=parent_run_id,
            **kwargs,
        )
        session.add(run)
        await session.flush()
        return run


async def _spawn(
    factory: async_sessionmaker[AsyncSession],
    settings: Any,
    parent: Run,
    *,
    params: dict[str, Any] | None = None,
    graph: Any = None,
    tenant_id: str = TENANT,
):
    async with session_scope(factory) as session:
        return await subagents.spawn_subagent(
            session,
            parent=parent,
            params=dict(PARAMS if params is None else params),
            settings=settings,
            factory=factory,
            run_graph=graph if graph is not None else _StubGraph(),
            store=None,
            workspace=None,
            downloader=None,
            context=None,
        )


async def _children(
    factory: async_sessionmaker[AsyncSession], parent_id: str
) -> list[Run]:
    async with session_scope(factory) as session:
        rows = await session.scalars(
            select(Run)
            .where(Run.parent_run_id == parent_id)
            .order_by(Run.created_at, Run.id)
        )
        return list(rows.all())


async def _events(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[RunEvent]:
    async with session_scope(factory) as session:
        rows = await session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        )
        return list(rows.all())


async def _item_payloads(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[dict[str, Any]]:
    return [
        event.payload
        for event in await _events(factory, run_id)
        if event.type == items.EVENT_TYPE
    ]


# ---- 工具面 ----


def test_spec_is_write_risk_and_declares_limits(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    tool = subagents.build_spawn_tool(factory, settings=settings)
    assert tool.name == subagents.SPAWN_TOOL_NAME
    # 建 run/写事件:WRITE 级;不进人工确认闸
    assert tool.risk is ToolRisk.WRITE
    schema = tool.parameters
    assert schema["required"] == ["title", "task"]
    assert schema["properties"]["tools"]["maxItems"] == settings.subagent_max_tools
    assert schema["properties"]["timeout_seconds"]["minimum"] == 30
    assert schema["properties"]["timeout_seconds"]["maximum"] == 1800
    assert tool.description


async def test_tool_requires_run_context(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    tool = subagents.build_spawn_tool(factory, settings=settings)
    with bind_tool_context(ToolContext(tenant_id=TENANT)):
        payload = json.loads(await tool.handler(dict(PARAMS)))
    assert payload["ok"] is False
    assert payload["error"] == "subagent.invalid_arguments"


async def test_tool_rejects_other_tenant(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    tool = subagents.build_spawn_tool(factory, settings=settings)
    parent = await _create_parent(factory)
    with bind_tool_context(ToolContext(tenant_id=OTHER, run_id=parent.id)):
        payload = json.loads(await tool.handler(dict(PARAMS)))
    assert payload["ok"] is False
    assert await _children(factory, parent.id) == []


class _StubContainer(ContainerAssemblyMixin):
    """只带 ``_load_extras`` 需要的属性的容器桩(其余依赖 getattr 兜底)。"""

    def __init__(
        self, settings: Any, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.settings = settings
        self.session_factory = factory
        self.skill_runner = None
        self.mcp_stdio_pool = None
        self.context_policy = None
        self.tool_offloader = None


async def _extra_names(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> set[str]:
    extras = await _StubContainer(settings, factory)._extras_factory()(TENANT)
    return {spec.name for spec in extras}


async def test_spawn_tool_mounted_only_in_top_level_run(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    child = await _create_parent(
        factory,
        parent_kind=PARENT_KIND_SUBAGENT,
        parent_run_id=parent.id,
    )
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=parent.id)):
        assert subagents.SPAWN_TOOL_NAME in await _extra_names(factory, settings)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=child.id)):
        # 子代理不能再派生(深度上限 1 在工具面就掐断)
        assert subagents.SPAWN_TOOL_NAME not in await _extra_names(factory, settings)
    with bind_tool_context(ToolContext(tenant_id=TENANT)):
        assert subagents.SPAWN_TOOL_NAME not in await _extra_names(factory, settings)


async def test_spawn_tool_honours_switch(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    disabled = settings.model_copy(update={"subagent_enabled": False})
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=parent.id)):
        assert subagents.SPAWN_TOOL_NAME not in await _extra_names(factory, disabled)


# ---- 参数校验 ----


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"task": "x"}, "subagent.invalid_arguments"),
        ({"title": "  ", "task": "x"}, "subagent.invalid_arguments"),
        ({"title": "x" * 121, "task": "x"}, "subagent.invalid_arguments"),
        ({"title": "t", "task": "x" * 4001}, "subagent.invalid_arguments"),
        (
            {"title": "t", "task": "x", "agent_name": "n" * 41},
            "subagent.invalid_arguments",
        ),
        ({"title": "t", "task": "x", "agent_name": 3}, "subagent.invalid_arguments"),
        ({"title": "t", "task": "x", "instructions": 3}, "subagent.invalid_arguments"),
        (
            {"title": "t", "task": "x", "instructions": "i" * 4001},
            "subagent.invalid_arguments",
        ),
        ({"title": "t", "task": "x", "tools": "read"}, "subagent.invalid_arguments"),
        ({"title": "t", "task": "x", "tools": [3]}, "subagent.invalid_arguments"),
        (
            {"title": "t", "task": "x", "timeout_seconds": 29},
            "subagent.invalid_arguments",
        ),
        (
            {"title": "t", "task": "x", "timeout_seconds": 1801},
            "subagent.invalid_arguments",
        ),
        (
            {"title": "t", "task": "x", "timeout_seconds": "600"},
            "subagent.invalid_arguments",
        ),
    ],
)
async def test_invalid_arguments_do_not_create_child(
    factory: async_sessionmaker[AsyncSession],
    settings: Any,
    params: dict[str, Any],
    code: str,
) -> None:
    parent = await _create_parent(factory)
    payload = await _spawn(factory, settings, parent, params=params)
    assert payload["ok"] is False
    assert payload["error"] == code
    assert await _children(factory, parent.id) == []
    assert await _item_payloads(factory, parent.id) == []


async def test_too_many_tools_rejected(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    params = {
        **PARAMS,
        "tools": [f"tool-{i}" for i in range(settings.subagent_max_tools + 1)],
    }
    payload = await _spawn(factory, settings, parent, params=params)
    assert payload["error"] == "subagent.invalid_arguments"
    assert await _children(factory, parent.id) == []


# ---- 上限与工具面继承 ----


async def test_limit_exceeded_after_quota(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    limited = settings.model_copy(update={"subagent_max_per_run": 1})
    first = await _spawn(factory, limited, parent)
    assert first["ok"] is True
    second = await _spawn(factory, limited, parent)
    assert second["ok"] is False
    assert second["error"] == "subagent.limit_exceeded"
    assert len(await _children(factory, parent.id)) == 1


async def test_depth_exceeded_for_subagent(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    top = await _create_parent(factory)
    child = await _create_parent(
        factory, parent_kind=PARENT_KIND_SUBAGENT, parent_run_id=top.id
    )
    payload = await _spawn(factory, settings, child)
    assert payload["ok"] is False
    assert payload["error"] == "subagent.depth_exceeded"


async def test_tools_must_be_subset_of_parent_allowlist(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory, skill_tools=["read"])
    payload = await _spawn(
        factory, settings, parent, params={**PARAMS, "tools": ["read", "fetch"]}
    )
    assert payload["error"] == "subagent.tools_not_subset"
    assert await _children(factory, parent.id) == []


async def test_tools_without_parent_allowlist_only_length_checked(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(factory)
    payload = await _spawn(
        factory, settings, parent, params={**PARAMS, "tools": ["read", "fetch"]}
    )
    assert payload["ok"] is True
    child = (await _children(factory, parent.id))[0]
    assert child.agent_tools == ["read", "fetch"]


async def test_tools_inherited_and_spawn_itself_stripped(
    factory: async_sessionmaker[AsyncSession], settings: Any
) -> None:
    parent = await _create_parent(
        factory, skill_tools=[subagents.SPAWN_TOOL_NAME, "read", "read"]
    )
    payload = await _spawn(factory, settings, parent)
    assert payload["ok"] is True
    child = (await _children(factory, parent.id))[0]
    # 父级有效白名单继承下来、去重、且不含 spawn 自身(防递归)
    assert child.agent_tools == ["read"]
