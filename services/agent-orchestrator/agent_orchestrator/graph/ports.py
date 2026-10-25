"""执行图与 services 层之间的端口契约(架构 §4 依赖倒置)。

graph 包只依赖这里的 Protocol:节点需要的外界能力——模型调用、工具注册表、
token 预算、审批策略、正文增量——全部由 services 层在装配处注入(实现见
``services/runs.py`` 的 ``_graph_ports``),因此 ``graph/`` 不
import ``agent_orchestrator.services``,依赖方向固定为 services → graph。

运行期载体是 ``graph.runtime.GraphRuntime``:由调用方通过 LangGraph 的
invocation config 显式传入(键见 ``GRAPH_RUNTIME_KEY``),不使用进程级
上下文变量。未注入时回退到默认实例,只发生在直跑节点的单测里。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

#: 工具风险值域里"危险操作"的取值(与 services.tools.ToolRisk.DANGER 对齐)。
RISK_DANGER = "danger"

#: 工具形态值域(与 common.tools.ToolKind 对齐):普通可执行 vs 需要用户输入。
#: interaction 工具不能 execute,只能由 execute 节点识别后走 ask_gate 中断。
KIND_FUNCTION = "function"
KIND_INTERACTION = "interaction"


class ApprovalDecision(StrEnum):
    """审批策略求值结果(graph 侧值域,services 侧枚举值与之对齐)。"""

    AUTO = "auto"
    CONFIRM = "confirm"
    DENY = "deny"


class DeltaSink(Protocol):
    """正文增量接收方(services 层实现):按批落成 run 事件。"""

    async def push(self, text: str, phase: str) -> None: ...


class ToolCallPort(Protocol):
    """模型请求的一次工具调用(arguments 为 JSON 字符串原文)。"""

    id: str
    name: str
    arguments: str


class ToolReplyPort(Protocol):
    """模型回复:正文与(可选)工具调用。"""

    content: str
    tool_calls: Sequence[ToolCallPort]


class LLMPort(Protocol):
    """模型调用端口:OpenAI 风格消息进,正文/工具调用出。"""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ToolReplyPort: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]: ...


class ToolkitPort(Protocol):
    """工具注册表端口(按名字执行工具、读取风险等级)。"""

    def __len__(self) -> int: ...

    @property
    def has_dangerous(self) -> bool: ...

    def extended(self, specs: list[Any]) -> ToolkitPort: ...

    def restricted(self, allowed: list[str]) -> ToolkitPort: ...

    def risk(self, name: str) -> str: ...

    def kind(self, name: str) -> str:
        """工具形态:KIND_FUNCTION 或 KIND_INTERACTION(未知名字回落 function)。"""
        ...

    def validate_call(self, name: str, arguments: str) -> list[str]:
        """执行前参数预校验;返回错误描述列表,空列表表示通过。

        交互工具(ask_question)在中断前必须校验参数——中断点之前零副作用,
        参数非法时回 tool_error 给模型重试,而不是挂起一个坏问题。
        """
        ...

    def schemas(self) -> list[dict[str, Any]]: ...

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = False,
    ) -> str:
        """执行一次工具调用(失败以错误信封回喂,不向图抛异常)。

        - tool_call_id:模型给出的调用 id,用于服务端幂等与审计关联;
          缺省由实现生成一个;
        - approved:审批流程已放行(策略 auto 或人工确认之后),**不等于**
          人工确认本身——对话路径/只读调用不传,执行图里确认后的写操作才传
          True,服务端据此校验写操作的风险下限。
        """
        ...


class UsagePort(Protocol):
    """run 级 token 预算闸(无采集器/未配置时由实现自行 no-op)。"""

    def check_budget(self) -> None: ...


class ApprovalPort(Protocol):
    """审批策略引擎:给定租户策略与工具风险,求本次调用的审批动作。"""

    def decide(
        self,
        policies: Sequence[Mapping[str, str]] | None,
        tool: str,
        risk: str,
    ) -> ApprovalDecision: ...


#: 租户 → 基础工具注册表(services 层按需装配,可为 None 表示无工具)。
ToolkitFactory = Callable[[str], ToolkitPort]
#: 租户 → 外部工具(如 MCP)的 specs;graph 侧不关心具体类型,交给注册表合并。
ToolExtrasFactory = Callable[[str], Awaitable[list[Any]]]
#: 空工具注册表工厂:只有外部工具、没有基础工具工厂时用它兜底合并。
EmptyToolkitFactory = Callable[[], ToolkitPort]


class _NullUsage:
    """默认预算闸:不限制(只在未注入真实实现的单测路径生效)。"""

    def check_budget(self) -> None:
        return


class RiskDefaultApproval:
    """默认策略引擎:忽略租户策略表,只按风险等级回落到既有默认值。

    生产装配注入 services.approval 的真实实现(含租户策略表);这里只是
    未注入时的保守兜底——DANGER 一律要确认,其余自动放行。
    """

    def decide(
        self,
        policies: Sequence[Mapping[str, str]] | None,
        tool: str,
        risk: str,
    ) -> ApprovalDecision:
        del policies, tool
        if risk == RISK_DANGER:
            return ApprovalDecision.CONFIRM
        return ApprovalDecision.AUTO


@dataclass(frozen=True, slots=True)
class GraphPorts:
    """一次图调用的外界能力集合(不可变,由装配方构造)。"""

    usage: UsagePort = field(default_factory=_NullUsage)
    approval: ApprovalPort = field(default_factory=RiskDefaultApproval)
    #: 仅 extras、无基础工具时用来合并;None 表示装配方未提供(遇到即报错)。
    empty_toolkit: EmptyToolkitFactory | None = None
