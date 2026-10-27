"""任务清单 M4 单测:纯逻辑、todo_write 工具、读端与图内注入。

工具 handler 直接调用(绕过 registry),临时 sqlite 文件库 + 真实 Run 行;
覆盖写快照/落事件/revision 推进/幂等/参数非法/缺上下文与图内提示块注入,零网络。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.graph.nodes import (
    _execute_messages,
    _todos_state,
    _todos_update,
)
from agent_orchestrator.graph.todos import (
    TODO_STATUSES,
    render_todo_block,
    sanitize_todos,
    todo_counts,
    validate_todos,
)
from agent_orchestrator.models.runs import Run, RunEvent
from agent_orchestrator.services.todos import (
    TODO_TOOL_NAME,
    TODO_UPDATED_EVENT,
    build_todo_tool,
    list_todos,
)
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import _settings

TENANT = "t-1"
OTHER = "t-2"

ITEMS = [
    {"content": "收集本周进展", "status": "completed"},
    {"content": "整理成周报", "status": "in_progress"},
    {"content": "发给团队", "status": "pending"},
]


@pytest.fixture
async def factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose。"""

    settings = _settings(tmp_path, "todos.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


async def _create_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str = TENANT,
) -> str:
    async with session_scope(factory) as session:
        run = Run(tenant_id=tenant_id, title="周报", goal="整理本周日程")
        session.add(run)
        await session.flush()
        return run.id


def _tool(factory: async_sessionmaker[AsyncSession], *, max_items: int = 20):
    return build_todo_tool(factory, max_items=max_items, max_content_chars=200)


async def _events(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[RunEvent]:
    async with session_scope(factory) as session:
        rows = await session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        )
        return list(rows.all())


async def _stored_todos(
    factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[dict[str, str]]:
    async with session_scope(factory) as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return sanitize_todos(run.todos)


# ---- 纯逻辑 ----


def test_statuses_are_three_states() -> None:
    assert TODO_STATUSES == ("pending", "in_progress", "completed")


def test_counts_keep_fixed_keys() -> None:
    counts = todo_counts(ITEMS)
    assert counts == {"pending": 1, "in_progress": 1, "completed": 1}
    assert todo_counts([]) == {"pending": 0, "in_progress": 0, "completed": 0}


def test_sanitize_drops_unknown_shapes_and_trims() -> None:
    raw = [
        {"content": "  收集进展  ", "status": "pending"},
        {"content": "", "status": "pending"},
        {"content": "未知状态", "status": "blocked"},
        "不是对象",
        {"content": "完成项", "status": "completed", "extra": 1},
    ]
    assert sanitize_todos(raw) == [
        {"content": "收集进展", "status": "pending"},
        {"content": "完成项", "status": "completed"},
    ]
    assert sanitize_todos(None) == []
    assert sanitize_todos({"content": "x"}) == []


def test_validate_accepts_and_normalizes() -> None:
    todos = validate_todos(
        [{"content": "  收集进展 ", "status": "pending"}],
        max_items=20,
        max_content_chars=200,
    )
    assert todos == [{"content": "收集进展", "status": "pending"}]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (None, "必须是数组"),
        ([], "清单不能为空"),
        ([{"content": "a", "status": "pending"}] * 3, "最多 2 项"),
        ([{"content": "  ", "status": "pending"}], "content 不能为空"),
        ([{"content": "x" * 5, "status": "pending"}], "超过 4 字符"),
        ([{"content": "a", "status": "blocked"}], "status 必须是"),
        (
            [
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},
            ],
            "最多一项 in_progress",
        ),
    ],
)
def test_validate_rejects_illegal_arguments(raw, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_todos(raw, max_items=2, max_content_chars=4)


def test_render_block_marks_states_and_reports_progress() -> None:
    block = render_todo_block(ITEMS)
    assert block.splitlines()[0] == "当前任务清单(1/3 完成):"
    assert "- [x] 收集本周进展" in block
    assert "- [~] 整理成周报" in block
    assert "- [ ] 发给团队" in block
    assert render_todo_block([]) == ""
    assert render_todo_block(None) == ""


# ---- todo_write 工具 ----


def test_spec_declares_write_risk_and_schema(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    assert tool.name == "todo_write"
    # 写 run 状态:标 WRITE,chat 路径的只读裁剪不会留下它
    assert tool.risk is ToolRisk.WRITE
    schema = tool.parameters
    assert schema["type"] == "object"
    assert schema["required"] == ["todos"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["todos"]["maxItems"] == 20
    item = schema["properties"]["todos"]["items"]
    assert item["required"] == ["content", "status"]
    assert item["properties"]["status"]["enum"] == list(TODO_STATUSES)


async def test_write_persists_snapshot_and_event(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    run_id = await _create_run(factory)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        raw = await tool.handler({"todos": ITEMS})
        payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["updated"] is True
    assert payload["revision"] == 1
    assert payload["todos"] == ITEMS
    assert payload["counts"] == {
        "pending": 1,
        "in_progress": 1,
        "completed": 1,
    }
    assert "1/3" in payload["summary"]
    assert await _stored_todos(factory, run_id) == ITEMS

    events = await _events(factory, run_id)
    assert [event.type for event in events] == [TODO_UPDATED_EVENT]
    assert events[0].tenant_id == TENANT
    assert events[0].payload == {
        "todos": ITEMS,
        "counts": {"pending": 1, "in_progress": 1, "completed": 1},
        "revision": 1,
        "source": "tool",
    }


async def test_second_write_advances_revision(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    run_id = await _create_run(factory)
    changed = [{"content": "收集本周进展", "status": "completed"}]
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        first = json.loads(await tool.handler({"todos": ITEMS}))
        second = json.loads(await tool.handler({"todos": changed}))

    assert (first["revision"], second["revision"]) == (1, 2)
    assert second["todos"] == changed
    assert await _stored_todos(factory, run_id) == changed
    events = await _events(factory, run_id)
    assert [event.payload["revision"] for event in events] == [1, 2]


async def test_identical_list_is_idempotent(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    run_id = await _create_run(factory)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        first = json.loads(await tool.handler({"todos": ITEMS}))
        again = json.loads(await tool.handler({"todos": ITEMS}))

    assert first["updated"] is True
    assert again["updated"] is False
    # 幂等返回也带着同一个 revision,模型不会误以为版本倒退
    assert again["revision"] == 1
    assert len(await _events(factory, run_id)) == 1


async def test_invalid_arguments_return_error_without_side_effects(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    run_id = await _create_run(factory)
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        raw = await tool.handler({"todos": [{"content": "a", "status": "x"}]})
    payload = json.loads(raw)
    assert "error" in payload
    assert await _stored_todos(factory, run_id) == []
    assert await _events(factory, run_id) == []


async def test_write_requires_run_context(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    with bind_tool_context(ToolContext(tenant_id=TENANT)):
        payload = json.loads(await tool.handler({"todos": ITEMS}))
    assert "缺少工具上下文" in payload["error"]


async def test_write_rejects_other_tenant(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    run_id = await _create_run(factory)
    with bind_tool_context(ToolContext(tenant_id=OTHER, run_id=run_id)):
        payload = json.loads(await tool.handler({"todos": ITEMS}))
    assert "不属于当前租户" in payload["error"]
    assert await _stored_todos(factory, run_id) == []


async def test_write_reports_missing_run(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    with bind_tool_context(
        ToolContext(tenant_id=TENANT, run_id="00000000000000000000000000000000")
    ):
        payload = json.loads(await tool.handler({"todos": ITEMS}))
    assert "error" in payload


# ---- 读端 ----


async def test_read_end_returns_empty_before_first_write(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _create_run(factory)
    async with session_scope(factory) as session:
        todo_read = await list_todos(session, TENANT, run_id)
    assert todo_read.todos == []
    assert todo_read.counts.model_dump() == {
        "pending": 0,
        "in_progress": 0,
        "completed": 0,
    }
    assert todo_read.revision == 0


async def test_read_end_reflects_snapshot_and_latest_revision(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tool = _tool(factory)
    run_id = await _create_run(factory)
    changed = [{"content": "收集本周进展", "status": "completed"}]
    with bind_tool_context(ToolContext(tenant_id=TENANT, run_id=run_id)):
        await tool.handler({"todos": ITEMS})
        await tool.handler({"todos": changed})

    async with session_scope(factory) as session:
        todo_read = await list_todos(session, TENANT, run_id)
    assert todo_read.run_id == run_id
    assert [item.content for item in todo_read.todos] == ["收集本周进展"]
    assert todo_read.todos[0].status == "completed"
    assert todo_read.counts.completed == 1
    assert todo_read.revision == 2


# ---- 图内注入 ----


def test_todos_update_parses_tool_envelope() -> None:
    envelope = json.dumps({"ok": True, "todos": ITEMS}, ensure_ascii=False)
    assert _todos_update(TODO_TOOL_NAME, envelope) == ITEMS
    assert _todos_update("read", envelope) is None
    assert _todos_update(TODO_TOOL_NAME, "不是 JSON") is None
    assert _todos_update(TODO_TOOL_NAME, json.dumps({"ok": True})) is None
    assert _todos_update(TODO_TOOL_NAME, "") is None


def test_todos_state_omits_missing_update() -> None:
    assert _todos_state(ITEMS) == {"todos": ITEMS}
    assert _todos_state(None) == {}


def test_execute_messages_inject_latest_list() -> None:
    messages = _execute_messages({"goal": "整理日程", "todos": ITEMS})
    system = messages[0]["content"]
    assert "当前任务清单(1/3 完成):" in system
    assert "- [~] 整理成周报" in system
    # 注入是替换式:只出现一次(系统提示里一份,不随历史累积)
    assert system.count("当前任务清单") == 1


def test_execute_messages_skip_block_without_list() -> None:
    messages = _execute_messages({"goal": "整理日程"})
    assert "当前任务清单" not in messages[0]["content"]
