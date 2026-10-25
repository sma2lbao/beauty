"""图调用运行期依赖的显式注入载体(取代进程级上下文变量)。

services 层在每次 ``graph.ainvoke`` 的 config 里放入一个
:class:`GraphRuntime`,节点通过 :func:`runtime_from_config` 读取——
sink/预算/审批/工具工厂因此随调用显式传递,而不是挂在模块全局或
进程全局对象上。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_orchestrator.graph.delta import DeltaProjector
from agent_orchestrator.graph.ports import GraphPorts
from agent_orchestrator.graph.steps import StepProjector

#: LangGraph config 的 ``configurable`` 里存放 GraphRuntime 的键。
GRAPH_RUNTIME_KEY = "agent_orchestrator.graph_runtime"


@dataclass(frozen=True, slots=True)
class GraphRuntime:
    """一次图调用的端口集合 + 正文增量投影器 + 步骤采集器。"""

    ports: GraphPorts = field(default_factory=GraphPorts)
    delta: DeltaProjector = field(default_factory=DeltaProjector)
    steps: StepProjector = field(default_factory=StepProjector)


#: 未注入时的默认运行期:无预算限制、无增量、按风险默认审批。
DEFAULT_RUNTIME = GraphRuntime()


def runtime_from_config(config: Mapping[str, Any] | None) -> GraphRuntime:
    """从 LangGraph invocation config 取本次调用的运行期依赖。

    config 缺省(直跑节点的单测)或未装配时回退 :data:`DEFAULT_RUNTIME`,
    节点行为与"无流式/无预算"完全一致。
    """
    if not config:
        return DEFAULT_RUNTIME
    configurable = config.get("configurable")
    if isinstance(configurable, Mapping):
        runtime = configurable.get(GRAPH_RUNTIME_KEY)
        if isinstance(runtime, GraphRuntime):
            return runtime
    return DEFAULT_RUNTIME
