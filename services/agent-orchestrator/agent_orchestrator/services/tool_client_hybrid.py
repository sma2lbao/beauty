"""远端内置工具 + orchestrator 侧工具(MCP / skill_run)的合并视图。

自 tool_client.py 拆出。

名字先问远端(``contains`` 口径含旧别名),未命中再问旧 ``ToolRegistry``;
两边都不认识才回 ``tool.not_found`` 信封。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from agent_orchestrator.services.tool_client_support import _error_envelope
from agent_orchestrator.services.tools import ToolRegistry, ToolSpec

if TYPE_CHECKING:
    from agent_orchestrator.services.tool_client_remote import RemoteToolkit


class HybridToolkit:
    """远端内置工具 + orchestrator 侧工具(MCP / skill_run)的合并视图。"""

    def __init__(self, base: RemoteToolkit, extra: ToolRegistry) -> None:
        self._base = base
        self._extra = extra

    def __len__(self) -> int:
        return len(self._base) + len(self._extra)

    @property
    def names(self) -> list[str]:
        return [*self._base.names, *self._extra.names]

    @property
    def has_dangerous(self) -> bool:
        return self._base.has_dangerous or self._extra.has_dangerous

    def risk(self, name: str) -> str:
        return (
            self._base.risk(name)
            if _contains(self._base, name)
            else self._extra.risk(name)
        )

    def kind(self, name: str) -> str:
        return (
            self._base.kind(name)
            if _contains(self._base, name)
            else self._extra.kind(name)
        )

    def validate_call(self, name: str, arguments: str) -> list[str]:
        if _contains(self._base, name):
            return self._base.validate_call(name, arguments)
        return self._extra.validate_call(name, arguments)

    def with_interaction(self) -> HybridToolkit:
        return HybridToolkit(self._base.with_interaction(), self._extra)

    def schemas(self) -> list[dict[str, Any]]:
        return [*self._base.schemas(), *self._extra.schemas()]

    def extended(self, specs: list[ToolSpec]) -> HybridToolkit:
        return HybridToolkit(self._base, self._extra.extended(specs))

    def restricted(self, allowed: list[str]) -> HybridToolkit:
        return HybridToolkit(
            self._base.restricted(allowed), self._extra.restricted(allowed)
        )

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = False,
    ) -> str:
        if _contains(self._base, name):
            return await self._base.execute(
                name, arguments, tool_call_id=tool_call_id, approved=approved
            )
        if _contains(self._extra, name):
            return await self._extra.execute(
                name, arguments, tool_call_id=tool_call_id, approved=approved
            )
        return _error_envelope(
            "tool.not_found",
            f"未知工具: {name}",
            tool_call_id=tool_call_id or f"orch-{uuid.uuid4().hex}",
        )


def _contains(toolkit: Any, name: str) -> bool:
    """判断工具箱是否认识这个名字(远端与旧注册表接口口径不同)。"""

    hook = getattr(toolkit, "contains", None)
    if callable(hook):
        return bool(hook(name))
    names = getattr(toolkit, "names", None)
    return bool(names is not None and name in names)
