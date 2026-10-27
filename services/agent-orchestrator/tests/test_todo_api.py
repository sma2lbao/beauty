"""任务清单读端 API 与工具面挂载(M4)。

走真实 app(TestClient):写清单仍由 todo_write 工具完成(直调 handler),
读端走 GET /api/v1/runs/{id}/todos;工具面断言跨 run/chat 两种上下文,零网络。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app
from agent_orchestrator.services.todos import TODO_TOOL_NAME, build_todo_tool
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from tests.conftest import OTHER, TENANT, _create_run, _settings, verifier

ITEMS: list[dict[str, str]] = [
    {"content": "收集本周进展", "status": "completed"},
    {"content": "整理成周报", "status": "in_progress"},
]


def _write_todos(client: TestClient, run_id: str, todos: list[dict[str, str]]) -> None:
    """直调工具 handler(与图内调用同一条路径),写 runs.todos + 事件。"""

    factory = client.app.state.session_factory
    assert factory is not None

    async def _run() -> None:
        tool = build_todo_tool(factory)
        with bind_tool_context(ToolContext(tenant_id="t-1", run_id=run_id)):
            await tool.handler({"todos": todos})

    asyncio.run(_run())


def test_read_end_returns_empty_list_before_first_write(client: TestClient) -> None:
    run_id = _create_run(client)
    resp = client.get(f"/api/v1/runs/{run_id}/todos", headers=TENANT)

    assert resp.status_code == 200
    assert resp.json() == {
        "run_id": run_id,
        "todos": [],
        "counts": {"pending": 0, "in_progress": 0, "completed": 0},
        "revision": 0,
    }


def test_read_end_reflects_written_snapshot(client: TestClient) -> None:
    run_id = _create_run(client)
    _write_todos(client, run_id, ITEMS)

    body = client.get(f"/api/v1/runs/{run_id}/todos", headers=TENANT).json()
    assert body["todos"] == ITEMS
    assert body["counts"] == {"pending": 0, "in_progress": 1, "completed": 1}
    assert body["revision"] == 1


def test_read_end_is_tenant_isolated(client: TestClient) -> None:
    run_id = _create_run(client)
    _write_todos(client, run_id, ITEMS)

    # 其它租户看不到别人的 run:统一 404,不泄漏存在性
    assert client.get(f"/api/v1/runs/{run_id}/todos", headers=OTHER).status_code == 404


def test_read_end_404_for_unknown_run(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/runs/00000000000000000000000000000000/todos", headers=TENANT
    )
    assert resp.status_code == 404


def _extras(client: TestClient, context: ToolContext) -> list[Any]:
    factory = client.app.state.tool_extras_factory
    assert factory is not None
    with bind_tool_context(context):
        return asyncio.run(factory(context.tenant_id))


def test_extras_expose_todo_write_only_with_run_context(client: TestClient) -> None:
    run_ctx = ToolContext(tenant_id="t-1", run_id="r-1")
    run_names = [spec.name for spec in _extras(client, run_ctx)]
    chat_names = [spec.name for spec in _extras(client, ToolContext(tenant_id="t-1"))]

    assert TODO_TOOL_NAME in run_names
    # 对话路径没有 run,清单无处存:不该暴露一个永远不可用的工具
    assert TODO_TOOL_NAME not in chat_names


def _client_without_todo(tmp_path: Path) -> TestClient:
    settings: Settings = _settings(tmp_path, "todo-off.db", todo_enabled=False)
    return TestClient(create_app(settings, token_verifier=verifier()))


def test_extras_hide_todo_write_when_disabled(tmp_path: Path) -> None:
    with _client_without_todo(tmp_path) as client:
        run_ctx = ToolContext(tenant_id="t-1", run_id="r-1")
        names = [spec.name for spec in _extras(client, run_ctx)]
    assert TODO_TOOL_NAME not in names
