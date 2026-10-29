"""执行图节点:plan/execute/verify 接 llm-gateway(架构 §5),deliver 为桩。

约定(架构 §4 分层):节点是纯函数——只读 state、返回增量,
不碰数据库、不发事件;事务与事件在 services 层的图调用边界统一处理。

依赖注入(架构 §4 依赖倒置):节点需要的外界能力全部来自 graph.ports 的
Protocol,由 services 层在装配处通过 LangGraph invocation config 注入
(graph.runtime.GraphRuntime);因此本模块不依赖 services 包。

LLM 注入:builder 用 make_*_node(llm) 工厂装配;llm 为 None 时回退
确定性桩(dev/测试零外部依赖)。节点内 LLM 异常不捕获,由 services 层的
图调用边界统一转 failed 终态(§6.2)。

工具注入:execute 拿 toolkit_factory(租户 → 工具注册表);工具执行失败
不抛错,以错误文本回喂模型(§6.1),每次调用记入 state["tool_calls"]。

本模块是门面:实现按职责拆到同包子模块(toolkit/audit/messages/gates/ask/
execute/tool_exec),这里统一重导出;原 `from agent_orchestrator.graph.nodes
import X` 的 import 路径(含测试直接用的私有名 `_record`)保持不变。
"""

from __future__ import annotations

from agent_orchestrator.graph.deliver_nodes import (
    DELIVER_SYSTEM_PROMPT as DELIVER_SYSTEM_PROMPT,
)
from agent_orchestrator.graph.deliver_nodes import (
    VERIFY_SYSTEM_PROMPT as VERIFY_SYSTEM_PROMPT,
)
from agent_orchestrator.graph.deliver_nodes import _artifact as _artifact
from agent_orchestrator.graph.deliver_nodes import _parse_verdicts as _parse_verdicts
from agent_orchestrator.graph.deliver_nodes import _stub_deliver as _stub_deliver
from agent_orchestrator.graph.deliver_nodes import fail_node as fail_node
from agent_orchestrator.graph.deliver_nodes import (
    make_deliver_node as make_deliver_node,
)
from agent_orchestrator.graph.deliver_nodes import make_verify_node as make_verify_node
from agent_orchestrator.graph.nodes.ask import MAX_ANSWER_CHARS as MAX_ANSWER_CHARS
from agent_orchestrator.graph.nodes.ask import MAX_ASK_CYCLES as MAX_ASK_CYCLES
from agent_orchestrator.graph.nodes.ask import MAX_QUESTION_CHARS as MAX_QUESTION_CHARS
from agent_orchestrator.graph.nodes.ask import (
    QUESTION_DECLINED_REPLY as QUESTION_DECLINED_REPLY,
)
from agent_orchestrator.graph.nodes.ask import (
    QUESTION_TIMEOUT_REPLY as QUESTION_TIMEOUT_REPLY,
)
from agent_orchestrator.graph.nodes.ask import (
    _build_pending_question as _build_pending_question,
)
from agent_orchestrator.graph.nodes.ask import _question_update as _question_update
from agent_orchestrator.graph.nodes.ask import _safe_text as _safe_text
from agent_orchestrator.graph.nodes.ask import _tool_kind as _tool_kind
from agent_orchestrator.graph.nodes.ask import _validate_call as _validate_call
from agent_orchestrator.graph.nodes.ask import (
    format_question_answers as format_question_answers,
)
from agent_orchestrator.graph.nodes.ask import make_ask_gate_node as make_ask_gate_node
from agent_orchestrator.graph.nodes.audit import DECISION_APPROVED as DECISION_APPROVED
from agent_orchestrator.graph.nodes.audit import DECISION_AUTO as DECISION_AUTO
from agent_orchestrator.graph.nodes.audit import DECISION_DENIED as DECISION_DENIED
from agent_orchestrator.graph.nodes.audit import DETAIL_CHARS as DETAIL_CHARS
from agent_orchestrator.graph.nodes.audit import _elapsed_ms as _elapsed_ms
from agent_orchestrator.graph.nodes.audit import _maybe_offload as _maybe_offload
from agent_orchestrator.graph.nodes.audit import _record as _record
from agent_orchestrator.graph.nodes.audit import _tool_error as _tool_error
from agent_orchestrator.graph.nodes.audit import _tool_item_begin as _tool_item_begin
from agent_orchestrator.graph.nodes.audit import _tool_item_denied as _tool_item_denied
from agent_orchestrator.graph.nodes.audit import _tool_item_fail as _tool_item_fail
from agent_orchestrator.graph.nodes.audit import _tool_item_finish as _tool_item_finish
from agent_orchestrator.graph.nodes.execute import (
    MAX_CONFIRM_CYCLES as MAX_CONFIRM_CYCLES,
)
from agent_orchestrator.graph.nodes.execute import MAX_TOOL_ROUNDS as MAX_TOOL_ROUNDS
from agent_orchestrator.graph.nodes.execute import (
    make_execute_node as make_execute_node,
)
from agent_orchestrator.graph.nodes.gates import _gate_update as _gate_update
from agent_orchestrator.graph.nodes.gates import confirm_gate_node as confirm_gate_node
from agent_orchestrator.graph.nodes.gates import (
    make_approval_gate_node as make_approval_gate_node,
)
from agent_orchestrator.graph.nodes.messages import (
    EXECUTE_SYSTEM_PROMPT as EXECUTE_SYSTEM_PROMPT,
)
from agent_orchestrator.graph.nodes.messages import (
    _assistant_tool_message as _assistant_tool_message,
)
from agent_orchestrator.graph.nodes.messages import (
    _execute_messages as _execute_messages,
)
from agent_orchestrator.graph.nodes.messages import _todos_state as _todos_state
from agent_orchestrator.graph.nodes.messages import _todos_update as _todos_update
from agent_orchestrator.graph.nodes.messages import _tool_message as _tool_message
from agent_orchestrator.graph.nodes.tool_exec import (
    make_tool_exec_node as make_tool_exec_node,
)
from agent_orchestrator.graph.nodes.toolkit import _empty_toolkit as _empty_toolkit
from agent_orchestrator.graph.nodes.toolkit import (
    _has_dangerous_tools as _has_dangerous_tools,
)
from agent_orchestrator.graph.nodes.toolkit import _load_toolkit as _load_toolkit
from agent_orchestrator.graph.nodes.toolkit import _scoped_toolkit as _scoped_toolkit
from agent_orchestrator.graph.plan_nodes import PLAN_SYSTEM_PROMPT as PLAN_SYSTEM_PROMPT
from agent_orchestrator.graph.plan_nodes import _context_block as _context_block
from agent_orchestrator.graph.plan_nodes import (
    _conversation_block as _conversation_block,
)
from agent_orchestrator.graph.plan_nodes import _criteria_block as _criteria_block
from agent_orchestrator.graph.plan_nodes import (
    _instructions_block as _instructions_block,
)
from agent_orchestrator.graph.plan_nodes import _memory_block as _memory_block
from agent_orchestrator.graph.plan_nodes import _skill_block as _skill_block
from agent_orchestrator.graph.plan_nodes import _stub_plan as _stub_plan
from agent_orchestrator.graph.plan_nodes import _text_reply as _text_reply
from agent_orchestrator.graph.plan_nodes import _todo_block as _todo_block
from agent_orchestrator.graph.plan_nodes import make_plan_node as make_plan_node
