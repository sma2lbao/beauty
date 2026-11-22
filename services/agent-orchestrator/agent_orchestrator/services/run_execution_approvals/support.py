"""装配与上下文:审批适配器、图端口、工具上下文、线程 id、图配置与中断载荷。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.graph import delta
from agent_orchestrator.graph.items import ItemProjector
from agent_orchestrator.graph.ports import ApprovalDecision, GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.graph.steps import StepProjector
from agent_orchestrator.models.runs import Run
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import hooks as hooks_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.run_boundary import SessionBoundary
from agent_orchestrator.services.run_cancellation import RunCancellationProbe
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRegistry, ToolRisk

#: 日志器名称:包内各模块共用,保持拆分前的 logger 名不变。
LOGGER_NAME = "agent_orchestrator.services.run_execution_approvals"
_TERMINAL_OR_CANCELLED = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.BLOCKED.value,
        RunStatus.CANCELLED.value,
    }
)


class _ApprovalAdapter:
    """graph.ports.ApprovalPort 的 services 实现:包装账号审批策略引擎。

    依赖方向是 services → graph:节点只认 graph 侧的值域,策略表求值仍留在
    services.approval(纯函数),适配层只做类型/枚举对齐。
    """

    def decide(
        self,
        policies: Any,
        tool: str,
        risk: str,
        arguments: Any = None,
    ) -> ApprovalDecision:
        action = approval_service.decide(policies, tool, ToolRisk(risk), arguments)
        return ApprovalDecision(action.value)


def _graph_ports() -> GraphPorts:
    """装配 graph 侧端口:预算闸、审批策略、应用 hooks、空工具注册表兜底。"""
    return GraphPorts(
        usage=usage_service,
        approval=_ApprovalAdapter(),
        hooks=hooks_service.WebhookHookRunner(),
        empty_toolkit=lambda: ToolRegistry([]),
    )


_GRAPH_PORTS = _graph_ports()


def _tool_context(run: Run):
    """工具 handler 的来源上下文:stdio MCP 据此按对话/run 复用沙箱(P2)。"""
    return bind_tool_context(
        ToolContext(
            account_id=run.account_id,
            run_id=run.id,
            conversation_id=run.conversation_id,
            # 记忆工具按工作区归属读写(与 run 的工作区一致,§6.7)
            workspace_id=run.workspace_id,
            # run 创建时快照的 MCP 引用:工具装配(execution/extras)只暴露这些 server
            mcp_server_ids=tuple(run.mcp_server_ids or ()),
            # 连接器引用快照:工具目录只保留这些 provider 的连接器工具
            connector_providers=tuple(run.connector_providers or ()),
        )
    )


def _thread_id(run: Run) -> str:
    """graph 线程标识(P1-C):同一 run 的每次 blocked→resume 周期一个新线程。

    checkpoint 列表/回退/分叉都必须按同一规则拼线程 id,故集中在这里。
    """
    return f"{run.id}#{run.attempts}"


def _config(
    run: Run,
    projector: delta.DeltaProjector | None = None,
    step_projector: StepProjector | None = None,
    item_projector: ItemProjector | None = None,
    *,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    """graph 线程标识 + 本次调用的运行期依赖(端口/增量/步骤/过程项投影器显式注入)。

    同一 run 的每次 blocked→resume 周期一个新线程;运行期依赖随调用传递,
    节点不再读进程级上下文变量。给了执行会话(S3)就同时装上取消探针与
    节点边界提交:节点开工前把上一段变化落库,取消方在另一个连接上才写得
    进去,探针随后读到的就是"已取消"。只做 aupdate_state 的调用不传会话。
    """
    ports = _GRAPH_PORTS
    if session is not None:
        ports = replace(
            ports,
            cancel=RunCancellationProbe(session, run.id),
            boundary=SessionBoundary(session),
        )
    return {
        "configurable": {
            "thread_id": _thread_id(run),
            GRAPH_RUNTIME_KEY: GraphRuntime(
                ports=ports,
                delta=projector or delta.DeltaProjector(),
                steps=step_projector or StepProjector(),
                items=item_projector or ItemProjector(),
            ),
        }
    }


def _interrupt_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    """取本次图调用的中断载荷(设计 §10.3/§6.2)。

    LangGraph 在状态里放的是 Interrupt 对象列表,载荷在 .value;
    checkpoint 恢复/测试桩也可能直接给 dict。两种都归一成 dict,
    非 Mapping 一律 {} 交给"未知中断"的旧语义分支兜底。
    """
    interrupts = values.get("__interrupt__") or ()
    if not interrupts:
        return {}
    first = interrupts[0]
    value = getattr(first, "value", first)
    if isinstance(value, Mapping):
        return dict(value)
    return {}
