"""P1-F 图接线(§7.3):context_block 优先/回退 + offloader 注入。

节点是纯函数:直接 await 工厂产出的节点函数(只传 state),offloader 用假
实现记录调用并返回固定引用信封——不建图连外部、不读写文件。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent_orchestrator.graph import builder as builder_module
from agent_orchestrator.graph.builder import build_run_graph
from agent_orchestrator.graph.deliver_nodes import (
    DELIVER_SYSTEM_PROMPT,
    VERIFY_SYSTEM_PROMPT,
    make_deliver_node,
    make_verify_node,
)
from agent_orchestrator.graph.nodes import (
    EXECUTE_SYSTEM_PROMPT,
    _execute_messages,
    _record,
    make_execute_node,
    make_tool_exec_node,
)
from agent_orchestrator.graph.plan_nodes import (
    PLAN_SYSTEM_PROMPT,
    _context_block,
    make_plan_node,
)
from agent_orchestrator.graph.ports import ApprovalDecision, GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.services.llm import LLMReply, ToolCall
from tests.conftest import FakeLLM

CONTEXT_BLOCK = "## 工作区约定(AGENTS.md)\n- 提交信息用中文"
SKILL_PROMPT = "必须用表格汇报"
# 旧口径的注入串(plan_nodes._skill_block/_memory_block 的拼接格式)
SKILL_BLOCK = f"\n\n技能约束(必须遵循):\n{SKILL_PROMPT}"
MEMORY_RAW = "## 长期记忆(跨会话,仅供参考)\n- 用户偏好简洁"
MEMORY_BLOCK = f"\n\n{MEMORY_RAW}"
CONTEXT_SUFFIX = f"\n\n{CONTEXT_BLOCK}"

READ_OUTPUT = "README 内容:" + "行" * 300
OFFLOAD_ENVELOPE = '{"ok": true, "offloaded": true, "chars": 9000}'
OFFLOAD_PATH = "/workspace/.agent/offload/run-1/read-ab12cd34.txt"
OFFLOAD_RECORD: dict[str, Any] = {
    "offloaded": True,
    "offload_path": OFFLOAD_PATH,
    "original_chars": len(READ_OUTPUT),
}
READ_CALL = ToolCall(id="call-1", name="read", arguments='{"path": "README.md"}')


def _state(**overrides: Any) -> RunGraphState:
    """最小 state:只放节点会读的字段,其余靠 .get 缺省。"""
    state: dict[str, Any] = {
        "run_id": "run-1",
        "tenant_id": "t-1",
        "title": "整理本周日程",
        "goal": "把本周日程整理成表格",
        "acceptance_criteria": ["输出表格"],
    }
    state.update(overrides)
    return state  # type: ignore[return-value]


def _runtime(approval: Any = None) -> GraphRuntime:
    """显式注入审批端口(默认 RiskDefaultApproval:read 走 auto)。"""
    ports = GraphPorts(approval=approval) if approval is not None else GraphPorts()
    return GraphRuntime(ports=ports)


def _config(runtime: GraphRuntime) -> dict[str, Any]:
    return {"configurable": {GRAPH_RUNTIME_KEY: runtime}}


class _FakeOffloader:
    """假 offloader:记录调用并返回固定引用信封(不落盘)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def maybe_offload(self, tool_name: str, output: str) -> Any:
        self.calls.append((tool_name, output))
        return SimpleNamespace(content=OFFLOAD_ENVELOPE, record=dict(OFFLOAD_RECORD))


class _PassThroughOffloader:
    """假 offloader:阈值内原样返回(record=None),验证节点不做额外处理。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def maybe_offload(self, tool_name: str, output: str) -> Any:
        self.calls.append((tool_name, output))
        return SimpleNamespace(content=output, record=None)


class _FixedApproval:
    """固定审批结论:用于 DENY/CONFIRM 分支(这些分支不该 offload)。"""

    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision

    def decide(self, policies: Any, tool_name: str, risk: str) -> ApprovalDecision:
        return self.decision


class _FakeToolkit:
    """最小 ToolkitPort:一个 read 工具,记录每次真实执行。"""

    def __init__(self, output: str = READ_OUTPUT) -> None:
        self.output = output
        self.executed: list[str] = []

    def __len__(self) -> int:
        return 1

    def has_dangerous(self) -> bool:
        return False

    def risk(self, name: str) -> str:
        return "read"

    def kind(self, name: str) -> str:
        return "function"

    def validate_call(self, name: str, arguments: str) -> list[str]:
        return []

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": "read"}]

    def extended(self, extras: Any) -> _FakeToolkit:
        return self

    def restricted(self, allowed: list[str] | None) -> _FakeToolkit:
        return self

    def with_interaction(self) -> _FakeToolkit:
        return self

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = False,
    ) -> str:
        self.executed.append(name)
        return self.output


def _tool_messages(history: list[dict[str, Any]]) -> list[str]:
    """从 tool_history 里取出回喂模型的 tool 消息正文。"""
    return [str(m.get("content", "")) for m in history if m.get("role") == "tool"]


# --- 统一 _context_block:有 context_block 优先,否则回退旧 skill/memory ---


def test_context_block_prefers_new_block_and_skips_legacy() -> None:
    block = _context_block(
        _state(context_block=CONTEXT_BLOCK, skill_system_prompt=SKILL_PROMPT)
    )
    assert block == CONTEXT_SUFFIX
    assert SKILL_PROMPT not in block


def test_context_block_falls_back_to_legacy_blocks() -> None:
    block = _context_block(
        _state(skill_system_prompt=SKILL_PROMPT, memory_block=MEMORY_RAW)
    )
    assert block == SKILL_BLOCK + MEMORY_BLOCK


def test_context_block_empty_when_nothing_configured() -> None:
    assert _context_block(_state()) == ""


def test_state_declares_context_fields() -> None:
    """RunGraphState 增加 context_block/context_snapshot(旧 checkpoint 靠 .get)。"""
    annotations = RunGraphState.__annotations__
    assert "context_block" in annotations
    assert "context_snapshot" in annotations


# --- plan / execute / verify / deliver 的系统提示 ---


async def test_plan_node_uses_context_block() -> None:
    fake = FakeLLM(["# 计划"])
    await make_plan_node(fake)(
        _state(context_block=CONTEXT_BLOCK, skill_system_prompt=SKILL_PROMPT)
    )
    system = fake.calls[0][0]["content"]
    assert system == PLAN_SYSTEM_PROMPT + CONTEXT_SUFFIX
    assert SKILL_PROMPT not in system


async def test_plan_node_falls_back_to_legacy_blocks() -> None:
    fake = FakeLLM(["# 计划"])
    await make_plan_node(fake)(
        _state(skill_system_prompt=SKILL_PROMPT, memory_block=MEMORY_RAW)
    )
    assert fake.calls[0][0]["content"] == (
        PLAN_SYSTEM_PROMPT + SKILL_BLOCK + MEMORY_BLOCK
    )


def test_execute_messages_use_context_block() -> None:
    messages = _execute_messages(_state(context_block=CONTEXT_BLOCK))
    assert messages[0]["content"] == EXECUTE_SYSTEM_PROMPT + CONTEXT_SUFFIX


def test_execute_messages_fall_back_to_legacy_blocks() -> None:
    messages = _execute_messages(
        _state(skill_system_prompt=SKILL_PROMPT, memory_block=MEMORY_RAW)
    )
    assert messages[0]["content"] == (
        EXECUTE_SYSTEM_PROMPT + SKILL_BLOCK + MEMORY_BLOCK
    )


async def test_verify_and_deliver_use_context_block() -> None:
    verdict = '{"summary": "ok", "verdicts": {"输出表格": true}}'
    verify_fake = FakeLLM([verdict])
    await make_verify_node(verify_fake)(
        _state(context_block=CONTEXT_BLOCK, skill_system_prompt=SKILL_PROMPT)
    )
    assert verify_fake.calls[0][0]["content"] == VERIFY_SYSTEM_PROMPT + CONTEXT_SUFFIX

    deliver_fake = FakeLLM(["# 交付物"])
    await make_deliver_node(deliver_fake)(
        _state(context_block=CONTEXT_BLOCK, skill_system_prompt=SKILL_PROMPT)
    )
    assert deliver_fake.calls[0][0]["content"] == DELIVER_SYSTEM_PROMPT + CONTEXT_SUFFIX


async def test_verify_and_deliver_fall_back_to_legacy_blocks() -> None:
    verdict = '{"summary": "ok", "verdicts": {"输出表格": true}}'
    verify_fake = FakeLLM([verdict])
    await make_verify_node(verify_fake)(
        _state(skill_system_prompt=SKILL_PROMPT, memory_block=MEMORY_RAW)
    )
    assert verify_fake.calls[0][0]["content"] == (
        VERIFY_SYSTEM_PROMPT + SKILL_BLOCK + MEMORY_BLOCK
    )

    deliver_fake = FakeLLM(["# 交付物"])
    await make_deliver_node(deliver_fake)(
        _state(skill_system_prompt=SKILL_PROMPT, memory_block=MEMORY_RAW)
    )
    assert deliver_fake.calls[0][0]["content"] == (
        DELIVER_SYSTEM_PROMPT + SKILL_BLOCK + MEMORY_BLOCK
    )


# --- _record 的 offload 字段 ---


def test_record_merges_offload_fields() -> None:
    record = _record("read", "{}", OFFLOAD_ENVELOPE, offload=OFFLOAD_RECORD)
    assert record["offloaded"] is True
    assert record["offload_path"] == OFFLOAD_PATH
    assert record["original_chars"] == len(READ_OUTPUT)
    assert record["result_chars"] == len(OFFLOAD_ENVELOPE)


def test_record_without_offload_keeps_old_shape() -> None:
    record = _record("read", "{}", READ_OUTPUT)
    assert "offloaded" not in record
    assert record["result_chars"] == len(READ_OUTPUT)


# --- execute / tool_exec 的 offloader 注入 ---


async def test_execute_node_offloads_tool_output() -> None:
    toolkit = _FakeToolkit()
    offloader = _FakeOffloader()
    fake = FakeLLM([LLMReply(content="", tool_calls=(READ_CALL,)), "已读完"])
    node = make_execute_node(fake, lambda tenant: toolkit, offloader=offloader)
    update = await node(_state(), _config(_runtime()))

    assert toolkit.executed == ["read"]
    assert offloader.calls == [("read", READ_OUTPUT)]
    record = update["tool_calls"][0]
    assert record["offloaded"] is True
    assert record["offload_path"] == OFFLOAD_PATH
    assert record["original_chars"] == len(READ_OUTPUT)
    assert record["result_chars"] == len(OFFLOAD_ENVELOPE)
    # 回喂模型的是引用信封,不是原始长文
    assert _tool_messages(update["tool_history"]) == [OFFLOAD_ENVELOPE]


async def test_execute_node_without_offloader_keeps_raw_output() -> None:
    toolkit = _FakeToolkit()
    fake = FakeLLM([LLMReply(content="", tool_calls=(READ_CALL,)), "已读完"])
    update = await make_execute_node(fake, lambda tenant: toolkit)(_state())

    assert _tool_messages(update["tool_history"]) == [toolkit.output]
    record = update["tool_calls"][0]
    assert record["result_chars"] == len(toolkit.output)
    assert "offloaded" not in record


async def test_execute_node_passthrough_offloader_keeps_raw_output() -> None:
    """阈值内:offloader 返回原串 + record=None,记录形状与未注入时一致。"""
    toolkit = _FakeToolkit(output="短结果")
    offloader = _PassThroughOffloader()
    fake = FakeLLM([LLMReply(content="", tool_calls=(READ_CALL,)), "已读完"])
    node = make_execute_node(fake, lambda tenant: toolkit, offloader=offloader)
    update = await node(_state(), _config(_runtime()))

    assert offloader.calls == [("read", "短结果")]
    assert _tool_messages(update["tool_history"]) == ["短结果"]
    record = update["tool_calls"][0]
    assert "offloaded" not in record
    assert record["result_chars"] == len("短结果")


async def test_execute_node_deny_branch_does_not_offload() -> None:
    """DENY 分支只回错误信封、不执行工具,也不 offload。"""
    toolkit = _FakeToolkit()
    offloader = _FakeOffloader()
    fake = FakeLLM([LLMReply(content="", tool_calls=(READ_CALL,)), "已结束"])
    node = make_execute_node(fake, lambda tenant: toolkit, offloader=offloader)
    approval = _FixedApproval(ApprovalDecision.DENY)
    update = await node(_state(), _config(_runtime(approval)))

    assert toolkit.executed == []
    assert offloader.calls == []
    assert update["tool_calls"][0]["decision"] == "denied"


async def test_execute_node_confirm_branch_does_not_offload() -> None:
    """CONFIRM 分支只收集待确认写操作,不执行、不 offload。"""
    toolkit = _FakeToolkit()
    offloader = _FakeOffloader()
    fake = FakeLLM([LLMReply(content="", tool_calls=(READ_CALL,))])
    node = make_execute_node(fake, lambda tenant: toolkit, offloader=offloader)
    approval = _FixedApproval(ApprovalDecision.CONFIRM)
    update = await node(_state(), _config(_runtime(approval)))

    assert toolkit.executed == []
    assert offloader.calls == []
    assert [w["name"] for w in update["pending_writes"]] == ["read"]


async def test_tool_exec_node_offloads_output() -> None:
    toolkit = _FakeToolkit()
    offloader = _FakeOffloader()
    pending = [{"id": "call-1", "name": "read", "arguments": "{}"}]
    node = make_tool_exec_node(lambda tenant: toolkit, offloader=offloader)
    update = await node(_state(pending_writes=pending), _config(_runtime()))

    assert toolkit.executed == ["read"]
    assert offloader.calls == [("read", READ_OUTPUT)]
    record = update["tool_calls"][0]
    assert record["decision"] == "approved"
    assert record["offloaded"] is True
    assert record["original_chars"] == len(READ_OUTPUT)
    assert _tool_messages(update["tool_history"]) == [OFFLOAD_ENVELOPE]


async def test_tool_exec_node_missing_toolkit_skips_offload() -> None:
    """装配缺失的错误信封不 offload(ok=False 的消息不该被包装)。"""
    offloader = _FakeOffloader()
    pending = [{"id": "call-1", "name": "write", "arguments": "{}"}]
    node = make_tool_exec_node(None, offloader=offloader)
    update = await node(_state(pending_writes=pending), _config(_runtime()))

    assert offloader.calls == []
    record = update["tool_calls"][0]
    assert record["ok"] is False
    assert "offloaded" not in record


# --- builder 把 offloader 传给两个执行节点 ---


def test_build_run_graph_forwards_offloader(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_make_execute_node(
        llm: Any = None,
        toolkit_factory: Any = None,
        extras_factory: Any = None,
        **kw: Any,
    ) -> Any:
        captured["execute"] = kw.get("offloader")
        return lambda state, config=None: {}

    def fake_make_tool_exec_node(
        toolkit_factory: Any = None,
        extras_factory: Any = None,
        *,
        offloader: Any = None,
    ) -> Any:
        captured["tool_exec"] = offloader
        return lambda state, config=None: {}

    monkeypatch.setattr(builder_module, "make_execute_node", fake_make_execute_node)
    monkeypatch.setattr(builder_module, "make_tool_exec_node", fake_make_tool_exec_node)
    offloader = _FakeOffloader()
    build_run_graph(offloader=offloader)

    assert captured["execute"] is offloader
    assert captured["tool_exec"] is offloader
