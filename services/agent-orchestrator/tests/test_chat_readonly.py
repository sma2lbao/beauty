"""对话内工具装配(设计 §10):内置工具只读收窄,MCP 工具走既有口径。"""

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


async def test_chat_keeps_only_read_builtin_tools() -> None:
    toolkit = await _toolkit_for(
        lambda tenant: FakeToolkit(_registry()), None, "t-1", read_only=True
    )
    assert toolkit is not None
    assert toolkit.names == ["read"]
    assert str(toolkit.risk("read")) == "read"


async def test_chat_without_read_only_keeps_write_tools() -> None:
    toolkit = await _toolkit_for(lambda tenant: _registry(), None, "t-1")
    assert toolkit is not None
    assert toolkit.names == ["read", "write"]


async def test_chat_ensures_catalog_before_filtering() -> None:
    fake = FakeToolkit(_registry())
    toolkit = await _toolkit_for(lambda tenant: fake, None, "t-1", read_only=True)
    assert fake.ensured == 1
    assert toolkit is not None
    assert toolkit.names == ["read"]


async def test_chat_ensure_failure_degrades_without_tools() -> None:
    """目录拉取失败只降级(对话照常回答),不抛给调用方。"""

    fake = FakeToolkit(_registry(), fail_ready=True)
    toolkit = await _toolkit_for(lambda tenant: fake, None, "t-1", read_only=True)
    assert fake.ensured == 1
    assert toolkit is not None
    assert toolkit.names == ["read"]


async def test_chat_mcp_extras_are_not_read_only_filtered() -> None:
    """MCP 工具按 auto_approve 映射成 WRITE,对话里仍可用(既有口径)。"""

    async def extras_factory(tenant_id: str) -> list[ToolSpec]:
        return [_spec("mcp__mock__echo", ToolRisk.WRITE)]

    toolkit = await _toolkit_for(
        lambda tenant: _registry(), extras_factory, "t-1", read_only=True
    )
    assert toolkit is not None
    assert toolkit.names == ["read", "mcp__mock__echo"]


async def test_chat_extras_failure_degrades() -> None:
    async def broken_extras(tenant_id: str) -> list[ToolSpec]:
        raise RuntimeError("mcp down")

    toolkit = await _toolkit_for(
        lambda tenant: _registry(), broken_extras, "t-1", read_only=True
    )
    assert toolkit is not None
    assert toolkit.names == ["read"]


async def test_chat_without_builtin_tools_uses_extras_only() -> None:
    async def extras_factory(tenant_id: str) -> list[ToolSpec]:
        return [_spec("mcp__mock__echo", ToolRisk.READ)]

    toolkit = await _toolkit_for(None, extras_factory, "t-1", read_only=True)
    assert isinstance(toolkit, ToolRegistry)
    assert toolkit.names == ["mcp__mock__echo"]
