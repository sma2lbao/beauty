"""对话内工具装配(设计 §10):function 且非 danger 才暴露,MCP 工具走既有口径。

对话没有任务路径的审批闸,但写操作改为「就地确认」(§6.7),因此 toolkit 里
**保留 WRITE**(模型能看到、会落 pending 确认),只把 DANGER 与交互工具裁掉。
"""

from __future__ import annotations

import json

from agent_orchestrator.services.conversations_persist import _toolkit_for
from agent_orchestrator.services.tools import ToolRegistry, ToolRisk, ToolSpec


async def _handler(arguments: dict) -> str:
    return json.dumps({"ok": True, "arguments": arguments})


def _spec(name: str, risk: ToolRisk) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} 工具",
        parameters={"type": "object"},
        handler=_handler,
        risk=risk,
    )


def _registry() -> ToolRegistry:
    return ToolRegistry([_spec("read", ToolRisk.READ), _spec("write", ToolRisk.WRITE)])


class FakeToolkit:
    """带 ensure_ready 的假工具箱:记录调用,执行委托给注册表。"""

    def __init__(self, registry: ToolRegistry, *, fail_ready: bool = False) -> None:
        self._registry = registry
        self._fail_ready = fail_ready
        self.ensured = 0

    async def ensure_ready(self) -> None:
        self.ensured += 1
        if self._fail_ready:
            raise RuntimeError("tool-service down")

    def __len__(self) -> int:
        return len(self._registry)

    @property
    def names(self) -> list[str]:
        return self._registry.names

    @property
    def has_dangerous(self) -> bool:
        return self._registry.has_dangerous

    def risk(self, name: str) -> ToolRisk:
        return self._registry.risk(name)

    def schemas(self) -> list[dict]:
        return self._registry.schemas()

    def extended(self, specs: list[ToolSpec]) -> ToolRegistry:
        return self._registry.extended(specs)

    def restricted(self, allowed: list[str]) -> ToolRegistry:
        return self._registry.restricted(allowed)

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = False,
    ) -> str:
        return await self._registry.execute(
            name, arguments, tool_call_id=tool_call_id, approved=approved
        )


async def test_chat_keeps_write_builtin_tools() -> None:
    """WRITE 不再被裁掉:模型要能看到它,才能发起"待确认"的调用。"""

    toolkit = await _toolkit_for(
        lambda tenant: FakeToolkit(_registry()), None, "t-1", chat=True
    )
    assert toolkit is not None
    assert toolkit.names == ["read", "write"]
    assert str(toolkit.risk("read")) == "read"
    assert str(toolkit.risk("write")) == "write"


async def test_chat_drops_danger_tools() -> None:
    """DANGER 在对话里一律不暴露(提示改用「任务」)。"""

    registry = ToolRegistry(
        [
            _spec("read", ToolRisk.READ),
            _spec("write", ToolRisk.WRITE),
            _spec("publish", ToolRisk.DANGER),
        ]
    )
    toolkit = await _toolkit_for(lambda tenant: registry, None, "t-1", chat=True)
    assert toolkit is not None
    assert toolkit.names == ["read", "write"]


class FakeInteractionToolkit(FakeToolkit):
    """带交互工具的假工具箱:kind() 区分 function/interaction。"""

    def kind(self, name: str) -> str:
        return "interaction" if name == "ask_question" else "function"


def _interaction_registry() -> ToolRegistry:
    return ToolRegistry(
        [_spec("read", ToolRisk.READ), _spec("ask_question", ToolRisk.READ)]
    )


async def test_chat_without_flag_keeps_everything() -> None:
    toolkit = await _toolkit_for(lambda tenant: _registry(), None, "t-1")
    assert toolkit is not None
    assert toolkit.names == ["read", "write"]


async def test_chat_filters_interaction_tools() -> None:
    """对话没有人工应答通道:交互工具即便在目录里也要过滤掉(§10.9)。"""

    fake = FakeInteractionToolkit(_interaction_registry())
    full = await _toolkit_for(lambda tenant: fake, None, "t-1")
    assert full is not None
    assert full.names == ["read", "ask_question"]
    chat = await _toolkit_for(lambda tenant: fake, None, "t-1", chat=True)
    assert chat is not None
    assert chat.names == ["read"]
    assert [item["function"]["name"] for item in chat.schemas()] == ["read"]


async def test_chat_ensures_catalog_before_filtering() -> None:
    fake = FakeToolkit(_registry())
    toolkit = await _toolkit_for(lambda tenant: fake, None, "t-1", chat=True)
    assert fake.ensured == 1
    assert toolkit is not None
    assert toolkit.names == ["read", "write"]


async def test_chat_ensure_failure_degrades_without_tools() -> None:
    """目录拉取失败只降级(对话照常回答),不抛给调用方。"""

    fake = FakeToolkit(_registry(), fail_ready=True)
    toolkit = await _toolkit_for(lambda tenant: fake, None, "t-1", chat=True)
    assert fake.ensured == 1
    assert toolkit is not None
    assert toolkit.names == ["read", "write"]


async def test_chat_mcp_extras_are_kept() -> None:
    """MCP 工具按 auto_approve 映射成 WRITE;对话里保留(确认后执行)。"""

    async def extras_factory(tenant_id: str) -> list[ToolSpec]:
        return [_spec("mcp__mock__echo", ToolRisk.WRITE)]

    toolkit = await _toolkit_for(
        lambda tenant: _registry(), extras_factory, "t-1", chat=True
    )
    assert toolkit is not None
    assert toolkit.names == ["read", "write", "mcp__mock__echo"]


async def test_chat_extras_failure_degrades() -> None:
    async def broken_extras(tenant_id: str) -> list[ToolSpec]:
        raise RuntimeError("mcp down")

    toolkit = await _toolkit_for(
        lambda tenant: _registry(), broken_extras, "t-1", chat=True
    )
    assert toolkit is not None
    assert toolkit.names == ["read", "write"]


async def test_chat_without_builtin_tools_uses_extras_only() -> None:
    async def extras_factory(tenant_id: str) -> list[ToolSpec]:
        return [_spec("mcp__mock__echo", ToolRisk.READ)]

    toolkit = await _toolkit_for(None, extras_factory, "t-1", chat=True)
    assert isinstance(toolkit, ToolRegistry)
    assert toolkit.names == ["mcp__mock__echo"]
