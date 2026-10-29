"""执行图节点的工具装配辅助:基础工具 + 外部工具(MCP)加载与危险面判定。

自 graph/nodes.py 拆出,行为与签名不变。
"""

from __future__ import annotations

from agent_orchestrator.graph.ports import (
    ToolExtrasFactory,
    ToolkitFactory,
    ToolkitPort,
)
from agent_orchestrator.graph.runtime import GraphRuntime
from agent_orchestrator.graph.state import RunGraphState


def _scoped_toolkit(
    toolkit: ToolkitPort | None, allowed_tools: list[str] | None
) -> ToolkitPort | None:
    """按 Skill 的 tools 白名单裁剪(与 execute 的口径一致)。

    缺省(None)=不限制;空列表=技能声明不用任何工具(与 execute 的
    ``allowed is not None`` 口径对齐,否则空白名单技能会多一次无谓审批)。
    """
    if toolkit is None:
        return None
    return toolkit.restricted(allowed_tools) if allowed_tools is not None else toolkit


def _empty_toolkit(runtime: GraphRuntime) -> ToolkitPort:
    """只有外部工具、没有基础工具工厂时的兜底空注册表。

    具体类在 services 层(依赖方向:graph 不 import services),由装配方
    通过 GraphPorts.empty_toolkit 注入;未注入即视为装配缺失。
    """
    factory = runtime.ports.empty_toolkit
    if factory is None:
        raise RuntimeError("外部工具有 specs 但装配方未注入 empty_toolkit 工厂")
    return factory()


async def _load_toolkit(
    tenant_id: str,
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ToolExtrasFactory | None,
    runtime: GraphRuntime,
) -> ToolkitPort | None:
    """基础工具 + 外部工具(MCP)。外部工具加载失败不阻断 run。"""
    toolkit = toolkit_factory(tenant_id) if toolkit_factory else None
    if toolkit is not None:
        # 远端工具箱(RemoteToolkit)惰性加载目录;失败向上抛——工具服务开启
        # 时它是必需依赖,本进程没有本机直读兜底(设计 §16.1 R1)。
        ensure = getattr(toolkit, "ensure_ready", None)
        if ensure is not None:
            await ensure()
    if extras_factory is not None:
        try:
            extras = await extras_factory(tenant_id)
        except Exception:  # noqa: BLE001 - MCP 故障降级为基础工具
            extras = []
        if extras:
            base = toolkit if toolkit is not None else _empty_toolkit(runtime)
            toolkit = base.extended(extras)
    return toolkit


async def _has_dangerous_tools(
    state: RunGraphState,
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ToolExtrasFactory | None,
    runtime: GraphRuntime,
) -> bool:
    """本 run 实际可用的工具里有没有危险操作(决定审批闸要不要中断)。

    口径 = 与该 run 执行阶段相同的完整工具面:基础工具 + 引用的 MCP 工具,
    再按技能 tools 白名单裁剪。工厂缺失(没有工具可调)视为无危险——
    执行阶段也就不可能产生未经确认的对外副作用。
    """
    toolkit = await _load_toolkit(
        state.get("tenant_id", ""), toolkit_factory, extras_factory, runtime
    )
    scoped = _scoped_toolkit(toolkit, state.get("allowed_tools"))
    return scoped is not None and scoped.has_dangerous
