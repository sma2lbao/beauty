"""一次 run 执行的 trace 根。

没有这层时,后台 job 起的图没有父 span,每个节点 span 都自成 trace——同一 run
在 Langfuse 里散成多条;而且 session(conversation_id)与 user(account_id)
只能靠节点 span 各自重复写。这里在每次 `graph.ainvoke` 边界开一层
`run {run_id}` 并写 Trace 级属性(经 baggage 传播给节点/模型 span):

- 已有父 span(HTTP 同步触发、审批恢复)时它挂在请求 span 下,不另起 trace;
- trace 名 = `run {run_id}`;session = conversation_id;user = account_id;
- observation 类型为 `agent`,run_id/attempts/agent_name 进可过滤 metadata。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from common import langfuse
from opentelemetry import trace
from opentelemetry.trace import Tracer

_TRACER = trace.get_tracer("agent_orchestrator.run")


@contextmanager
def run_trace(run: Any, *, tracer: Tracer | None = None) -> Iterator[Any]:
    """包住一次图调用;run 只需带 id/account_id/conversation_id 等属性。"""

    tracer = tracer or _TRACER
    with (
        tracer.start_as_current_span(f"run {run.id}") as span,
        langfuse.langfuse_scope(
            trace_name=f"run {run.id}",
            session_id=run.conversation_id,
            user_id=run.account_id,
            tags=["agent-orchestrator"],
        ),
    ):
        metadata = {
            "run_id": run.id,
            "attempts": run.attempts,
            "agent_name": run.agent_name,
            "workspace_id": run.workspace_id,
            "parent_run_id": run.parent_run_id,
        }
        attributes = langfuse.observation_attributes(
            observation_type="agent",
            metadata={k: v for k, v in metadata.items() if v is not None},
        )
        attributes.update(
            {
                langfuse.GEN_AI_CONVERSATION_ID: run.conversation_id,
                langfuse.GEN_AI_AGENT_NAME: run.agent_name,
            }
        )
        present = {key: value for key, value in attributes.items() if value is not None}
        langfuse.apply_attributes(span, present)
        yield span
