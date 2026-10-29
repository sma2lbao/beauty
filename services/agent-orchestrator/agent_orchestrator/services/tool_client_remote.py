"""把远端工具目录包装成 graph 侧 ToolkitPort(自 tool_client.py 拆出)。

- schemas():默认只暴露可执行的普通工具(kind=function);交互工具
  (ask_question)只在执行图里经 with_interaction() 开启;
- kind()/validate_call():执行图据此识别 interaction 调用并在中断前校验参数;
- risk():返回旧枚举 read/write/danger,审批闸与 golden-run 断言无需改;
- execute():参数 JSON 解析失败/未知工具/服务不可达一律回错误信封。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from common.errors import UpstreamError
from common.tools import (
    ToolDescriptor,
    ToolExecuteRequest,
    ToolKind,
    ToolRisk,
    to_legacy_risk,
    validate_arguments,
)

from agent_orchestrator.services.tool_client_catalog import ToolCatalog
from agent_orchestrator.services.tool_client_hybrid import HybridToolkit
from agent_orchestrator.services.tool_client_support import (
    DEFAULT_TIMEOUT_GRACE_SECONDS,
    _error_envelope,
)
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import ToolRegistry, ToolSpec

if TYPE_CHECKING:
    from agent_orchestrator.services.tool_client_http import ToolClient


class RemoteToolkit:
    """把 tool-service 目录包装成 graph 侧 ToolkitPort(按租户实例化)。

    - schemas():默认只暴露可执行的普通工具(kind=function);交互工具
      (ask_question)只在执行图里经 with_interaction() 开启——对话路径默认
      不可见(设计 §10.9),避免闲聊里挂起等待用户输入;
    - kind()/validate_call():执行图据此识别 interaction 调用并在中断前校验参数;
    - risk():返回旧枚举 read/write/danger,审批闸与 golden-run 断言无需改;
    - execute():参数 JSON 解析失败/未知工具/服务不可达一律回错误信封。
    """

    def __init__(
        self,
        tenant_id: str,
        *,
        client: ToolClient,
        catalog: ToolCatalog,
        timeout_grace_seconds: float = DEFAULT_TIMEOUT_GRACE_SECONDS,
        allowed: frozenset[str] | None = None,
        interaction: bool = False,
    ) -> None:
        self._tenant_id = tenant_id
        self._client = client
        self._catalog = catalog
        self._timeout_grace = timeout_grace_seconds
        self._allowed = allowed
        # 交互工具默认隐藏;执行图装配时显式 with_interaction() 打开。
        self._interaction = interaction

    async def ensure_ready(self) -> None:
        """惰性加载目录;图节点/对话循环在拿到 toolkit 后显式等待一次。"""

        await self._catalog.ensure(self._client)

    def __len__(self) -> int:
        return len(self._model_tools())

    @property
    def names(self) -> list[str]:
        return [tool.name for tool in self._model_tools()]

    def contains(self, name: str) -> bool:
        canonical = self._canonical(name)
        return canonical is not None and canonical in {
            t.name for t in self._model_tools()
        }

    @property
    def has_dangerous(self) -> bool:
        return any(tool.risk is ToolRisk.DESTRUCTIVE for tool in self._model_tools())

    def risk(self, name: str) -> str:
        """旧枚举风险值(read/write/danger);未知工具按最严处理。"""

        descriptor = self._descriptor(name)
        if descriptor is None:
            return "danger"
        return to_legacy_risk(descriptor.risk)

    def kind(self, name: str) -> str:
        """工具形态(function/interaction);未知名字按 function 处理。"""

        descriptor = self._descriptor(name)
        return str(descriptor.kind) if descriptor is not None else "function"

    def validate_call(self, name: str, arguments: str) -> list[str]:
        """按目录里的 input_schema 预校验参数(中断前零副作用,故必须先校验)。"""

        descriptor = self._descriptor(name)
        if descriptor is None:
            return [f"未知工具: {name}"]
        try:
            payload = json.loads(arguments) if arguments else {}
        except ValueError as exc:
            return [f"工具参数不是合法 JSON: {exc}"]
        if not isinstance(payload, dict):
            return ["工具参数必须是 JSON 对象"]
        return [
            error.message
            for error in validate_arguments(descriptor.input_schema, payload)
        ]

    def with_interaction(self) -> RemoteToolkit:
        """打开 interaction 工具可见性(执行图专用;对话路径不调用)。"""

        return self._clone(interaction=True)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in self._model_tools()
        ]

    def extended(self, specs: list[ToolSpec]) -> HybridToolkit:
        """合并 orchestrator 侧工具(MCP / skill_run),内置工具仍在远端。"""

        return HybridToolkit(self, ToolRegistry(list(specs)))

    def restricted(self, allowed: list[str]) -> RemoteToolkit:
        """按 Skill/对话白名单裁剪;白名单里的旧别名映射回正式名。"""

        canonical: set[str] = set()
        for name in allowed:
            found = self._canonical(name)
            if found is not None:
                canonical.add(found)
        if self._allowed is not None:
            canonical &= self._allowed
        return self._clone(allowed=frozenset(canonical))

    def _clone(
        self,
        *,
        allowed: frozenset[str] | None = None,
        interaction: bool | None = None,
    ) -> RemoteToolkit:
        """复制工具箱:restricted/with_interaction 都走这里,保证标志不丢。"""

        return RemoteToolkit(
            self._tenant_id,
            client=self._client,
            catalog=self._catalog,
            timeout_grace_seconds=self._timeout_grace,
            allowed=self._allowed if allowed is None else allowed,
            interaction=self._interaction if interaction is None else interaction,
        )

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = False,
    ) -> str:
        """执行工具;契约同旧 ToolRegistry:永不向图抛异常。

        tool_call_id 是调用方给的幂等键(缺省生成 orch-*),approved 是审批
        流程放行的凭据(策略 auto / 人工确认之后)——这里只做透传,不再按
        风险自动补:是否放行由调用方(图节点/对话循环)决定。
        """

        call_id = tool_call_id or f"orch-{uuid.uuid4().hex}"
        descriptor = self._descriptor(name)
        if descriptor is None:
            return _error_envelope(
                "tool.not_found",
                f"未知工具: {name}",
                hint="只允许调用 schemas() 中列出的工具",
                tool_call_id=call_id,
            )
        if descriptor.kind is not ToolKind.FUNCTION:
            return _error_envelope(
                "tool.not_supported",
                f"工具 {descriptor.name} 需要人工交互,不能在执行图里直接调用",
                tool_call_id=call_id,
            )
        try:
            payload = json.loads(arguments) if arguments else {}
        except ValueError as exc:
            return _error_envelope(
                "tool.invalid_args",
                f"工具参数不是合法 JSON: {exc}",
                tool_call_id=call_id,
            )
        if not isinstance(payload, dict):
            return _error_envelope(
                "tool.invalid_args",
                "工具参数必须是 JSON 对象",
                tool_call_id=call_id,
            )
        context = current_tool_context()
        request = ToolExecuteRequest(
            tool_call_id=call_id,
            arguments=payload,
            run_id=context.run_id if context is not None else None,
            conversation_id=context.conversation_id if context is not None else None,
            # 审批凭据完全由调用方决定(执行图:策略 auto 或人工确认后传
            # True;对话路径恒 False,写工具由服务端风险下限兜底)。
            approved=approved,
        )
        timeout = descriptor.timeout_seconds + self._timeout_grace
        try:
            response = await asyncio.wait_for(
                self._client.execute(self._tenant_id, descriptor.name, request),
                timeout=timeout,
            )
        except TimeoutError:
            return _error_envelope(
                "tool.timeout",
                f"工具 {descriptor.name} 执行超时({timeout:g}s)",
                retryable=True,
                tool_call_id=call_id,
            )
        except UpstreamError as exc:
            return _error_envelope(
                exc.code,
                exc.message,
                hint="tool-service 暂不可用,可稍后重试",
                retryable=True,
                tool_call_id=call_id,
            )
        if not response.tool_call_id:
            # 服务端没回显幂等键时补上本次请求的,保证审计/回放能对上。
            response = replace(response, tool_call_id=call_id)
        return json.dumps(response.envelope(), ensure_ascii=False)

    def _model_tools(self) -> tuple[ToolDescriptor, ...]:
        """模型可见工具:function 恒可见,interaction 仅 with_interaction 后可见。"""

        return tuple(
            tool
            for tool in self._visible_tools()
            if tool.kind is ToolKind.FUNCTION
            or (self._interaction and tool.kind is ToolKind.INTERACTION)
        )

    def _visible_tools(self) -> tuple[ToolDescriptor, ...]:
        tools = self._catalog.descriptors
        if self._allowed is None:
            return tools
        return tuple(tool for tool in tools if tool.name in self._allowed)

    def _descriptor(self, name: str) -> ToolDescriptor | None:
        canonical = self._canonical(name)
        if canonical is None:
            return None
        for tool in self._visible_tools():
            if tool.name == canonical:
                return tool
        return None

    def _canonical(self, name: str) -> str | None:
        for tool in self._visible_tools():
            if tool.name == name:
                return tool.name
        for tool in self._visible_tools():
            if name in tool.aliases:
                return tool.name
        return None
