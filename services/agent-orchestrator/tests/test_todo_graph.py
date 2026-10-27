"""任务清单图接线(M4):工具结果回写 state、提示块注入。

节点是纯函数:直接 await 工厂产出的节点函数;工具用假 ToolkitPort 返回固定
信封,不建图、不连外部、不读写文件。
"""

from __future__ import annotations

import json
from typing import Any

from agent_orchestrator.graph.nodes import (
    make_execute_node,
    make_tool_exec_node,
)
from agent_orchestrator.graph.ports import GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.services.llm import LLMReply, ToolCall
from tests.conftest import FakeLLM

ITEMS: list[dict[str, str]] = [
    {"content": "收集本周进展", "status": "in_progress"},
    {"content": "整理成周报", "status": "pending"},
]
WRITE_CALL = ToolCall(
    id="call-1",
    name="todo_write",
    arguments=json.dumps({"todos": ITEMS}, ensure_ascii=False),
)


def _state(**overrides: Any) -> RunGraphState:
    state: dict[str, Any] = {
        "run_id": "run-1",
        "tenant_id": "t-1",
        "title": "周报",
        "goal": "整理本周日程",
        "acceptance_criteria": ["输出周报"],
    }
    state.update(overrides)
    return state  # type: ignore[return-value]


def _config() -> dict[str, Any]:
    return {"configurable": {GRAPH_RUNTIME_KEY: GraphRuntime(ports=GraphPorts())}}


class _TodoToolkit:
    """最小 ToolkitPort:只有 todo_write,返回固定信封。"""

    def __init__(self, envelope: str) -> None:
        self.envelope = envelope
        self.executed: list[str] = []

    def __len__(self) -> int:
        return 1

    def has_dangerous(self) -> bool:
        return False

    def risk(self, name: str) -> str:
        return "write"

    def kind(self, name: str) -> str:
        return "function"

    def validate_call(self, name: str, arguments: str) -> list[str]:
        return []

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": "todo_write"}]

    def extended(self, extras: Any) -> _TodoToolkit:
        return self

    def restricted(self, allowed: list[str] | None) -> _TodoToolkit:
        return self

    def with_interaction(self) -> _TodoToolkit:
        return self

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = True,
    ) -> str:
        self.executed.append(name)
        return self.envelope


def _envelope(*, ok: bool = True) -> str:
    return json.dumps(
        {"ok": ok, "updated": ok, "todos": ITEMS, "revision": 1},
        ensure_ascii=False,
    )


async def test_execute_node_writes_todos_back_to_state() -> None:
    toolkit = _TodoToolkit(_envelope())
    fake = FakeLLM([LLMReply(content="", tool_calls=(WRITE_CALL,)), "已整理"])
    node = make_execute_node(fake, lambda tenant: toolkit)
    update = await node(_state(), _config())

    assert toolkit.executed == ["todo_write"]
    assert update["todos"] == ITEMS
    # 工具结果原样进消息历史:模型下一轮读得到自己写的清单
    tool_messages = [
        message for message in update["tool_history"] if message["role"] == "tool"
    ]
    assert tool_messages[0]["content"] == _envelope()


async def test_execute_node_skips_write_back_for_error_envelope() -> None:
    toolkit = _TodoToolkit(json.dumps({"error": "清单不能为空"}))
    fake = FakeLLM([LLMReply(content="", tool_calls=(WRITE_CALL,)), "已整理"])
    node = make_execute_node(fake, lambda tenant: toolkit)
    update = await node(_state(todos=ITEMS), _config())

    # 失败结果不动 state:清单仍是上一份,不会因为一次坏参数被清空
    assert "todos" not in update


async def test_tool_exec_node_writes_todos_back_to_state() -> None:
    toolkit = _TodoToolkit(_envelope())
    pending = [
        {"id": "call-1", "name": "todo_write", "arguments": WRITE_CALL.arguments}
    ]
    node = make_tool_exec_node(lambda tenant: toolkit)
    update = await node(_state(pending_writes=pending), _config())

    assert toolkit.executed == ["todo_write"]
    assert update["todos"] == ITEMS


async def test_execute_prompt_includes_latest_list_from_state() -> None:
    seen: list[list[dict[str, Any]]] = []

    class _RecordingLLM(FakeLLM):
        async def complete(self, messages: list[dict[str, Any]], **kwargs: Any):
            seen.append(messages)
            return LLMReply(content="结果小结")

    node = make_execute_node(_RecordingLLM([]), lambda tenant: _TodoToolkit(""))
    await node(_state(todos=ITEMS), _config())

    system = seen[0][0]["content"]
    assert "当前任务清单(0/2 完成):" in system
    assert "- [~] 收集本周进展" in system
