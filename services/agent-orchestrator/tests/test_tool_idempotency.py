"""S1:工具调用的稳定幂等键。

节点是纯函数,直接 await;工具用假 ToolkitPort 记录收到的键,不建图、零网络。
"""

from __future__ import annotations

import json
from typing import Any

from agent_orchestrator.graph.idempotency import tool_idempotency_key
from agent_orchestrator.graph.nodes import make_execute_node, make_tool_exec_node
from agent_orchestrator.graph.ports import GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.services.llm import LLMReply, ToolCall
from tests.conftest import FakeLLM

WRITE_ARGS = json.dumps({"path": "a.md", "content": "hello"}, ensure_ascii=False)


def _state(**overrides: Any) -> RunGraphState:
    state: dict[str, Any] = {
        "run_id": "run-1",
        "account_id": "t-1",
        "title": "写文件",
        "goal": "把结果写进 a.md",
        "acceptance_criteria": ["a.md 存在"],
    }
    state.update(overrides)
    return state  # type: ignore[return-value]


def _config() -> dict[str, Any]:
    return {"configurable": {GRAPH_RUNTIME_KEY: GraphRuntime(ports=GraphPorts())}}


class _KeyRecorderToolkit:
    """最小 ToolkitPort:记录每次 execute 收到的幂等键。"""

    def __init__(self, risk: str) -> None:
        self._risk = risk
        self.keys: list[str | None] = []
        self.executed: list[str] = []

    def __len__(self) -> int:
        return 1

    @property
    def has_dangerous(self) -> bool:
        return self._risk == "destructive"

    def risk(self, name: str) -> str:
        return self._risk

    def kind(self, name: str) -> str:
        return "function"

    def validate_call(self, name: str, arguments: str) -> list[str]:
        return []

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": "write"}]

    def extended(self, extras: Any) -> _KeyRecorderToolkit:
        return self

    def restricted(self, allowed: list[str] | None) -> _KeyRecorderToolkit:
        return self

    def with_interaction(self) -> _KeyRecorderToolkit:
        return self

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = True,
        idempotency_key: str | None = None,
    ) -> str:
        self.executed.append(name)
        self.keys.append(idempotency_key)
        return json.dumps({"ok": True, "path": "a.md"}, ensure_ascii=False)


def test_key_is_stable_for_reordered_arguments() -> None:
    first = tool_idempotency_key(
        run_id="run-1",
        node="execute",
        tool_name="write",
        arguments='{"path": "a.md", "content": "hi"}',
        risk="write",
    )
    second = tool_idempotency_key(
        run_id="run-1",
        node="execute",
        tool_name="write",
        arguments='{"content": "hi", "path": "a.md"}',
        risk="write",
    )

    assert first is not None
    assert first.startswith("orch1:")
    assert first == second


def test_key_changes_with_arguments_tool_and_run() -> None:
    base = dict(
        run_id="run-1",
        node="execute",
        tool_name="write",
        arguments='{"path": "a.md"}',
        risk="write",
    )
    assert tool_idempotency_key(**base) != tool_idempotency_key(
        **{**base, "arguments": '{"path": "b.md"}'}
    )
    assert tool_idempotency_key(**base) != tool_idempotency_key(
        **{**base, "tool_name": "todo_write"}
    )
    assert tool_idempotency_key(**base) != tool_idempotency_key(
        **{**base, "run_id": "run-2"}
    )


def test_key_is_none_for_reads_and_missing_run() -> None:
    base = dict(
        node="execute",
        tool_name="read",
        arguments='{"path": "a.md"}',
    )
    assert tool_idempotency_key(**base, run_id="run-1", risk="read") is None
    assert tool_idempotency_key(**base, run_id=None, risk="write") is None
    # 参数不是 JSON 对象:交给工具自己报错,不去重
    assert (
        tool_idempotency_key(
            run_id="run-1",
            node="execute",
            tool_name="write",
            arguments="[1]",
            risk="write",
        )
        is None
    )


async def test_execute_node_survives_regenerated_call_id() -> None:
    toolkit = _KeyRecorderToolkit("write")
    first_call = ToolCall(id="call-attempt-1", name="write", arguments=WRITE_ARGS)
    second_call = ToolCall(id="call-attempt-2", name="write", arguments=WRITE_ARGS)

    for call in (first_call, second_call):
        node = make_execute_node(
            FakeLLM([LLMReply(content="", tool_calls=(call,)), "已写入"]),
            lambda account: toolkit,
        )
        await node(_state(), _config())

    assert toolkit.executed == ["write", "write"]
    assert toolkit.keys[0] is not None
    assert toolkit.keys[0] == toolkit.keys[1]


async def test_execute_node_omits_key_for_read_tools() -> None:
    toolkit = _KeyRecorderToolkit("read")
    call = ToolCall(id="call-1", name="read", arguments='{"path": "a.md"}')
    node = make_execute_node(
        FakeLLM([LLMReply(content="", tool_calls=(call,)), "读完了"]),
        lambda account: toolkit,
    )

    await node(_state(), _config())

    assert toolkit.keys == [None]


async def test_tool_exec_node_passes_stable_key() -> None:
    toolkit = _KeyRecorderToolkit("write")
    keys: list[str | None] = []
    for call_id in ("confirmed-1", "confirmed-2"):
        node = make_tool_exec_node(lambda account: toolkit)
        pending = [{"id": call_id, "name": "write", "arguments": WRITE_ARGS}]
        update = await node(_state(pending_writes=pending), _config())
        assert update["tool_history"]
    keys.extend(toolkit.keys)

    assert len(keys) == 2
    assert keys[0] is not None
    assert keys[0] == keys[1]
