"""run 的 checkpoint 列表(P1-C,见 docs/design/recovery.md §5)。

数据源是执行图的 state history(LangGraph checkpointer),不是本服务的表:
checkpoint 是图线程的产物,按其 configurable.checkpoint_id 寻址。列表按最新
在前返回,供控制台选择"回退到哪一步"或"从哪里分叉"。

读端点不写 audit_logs(与 events/steps/items 等只读回放一致)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Run
from agent_orchestrator.schemas.runs import (
    CheckpointRead,
    CheckpointSummary,
)
from agent_orchestrator.services.run_execution_approvals import _thread_id
from agent_orchestrator.services.run_lifecycle import require_run


def thread_config(run: Run) -> dict[str, Any]:
    """run 当前 attempt 的线程配置(checkpoint 列表/回退共用)。"""
    return {"configurable": {"thread_id": _thread_id(run)}}


def checkpoint_id_of(snapshot: Any) -> str | None:
    """从 StateSnapshot.config 取 checkpoint_id。"""
    config = getattr(snapshot, "config", None) or {}
    configurable = config.get("configurable") or {}
    return configurable.get("checkpoint_id")


def parent_checkpoint_id_of(snapshot: Any) -> str | None:
    """从 StateSnapshot.parent_config 取父 checkpoint_id(首条历史为空)。"""
    config = getattr(snapshot, "parent_config", None) or {}
    configurable = config.get("configurable") or {}
    return configurable.get("checkpoint_id")


def _parse_created_at(raw: Any) -> datetime | None:
    """checkpoint 的 created_at 是 ISO 字符串(LangGraph 口径),解析失败按 None。"""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _summary(values: dict[str, Any]) -> CheckpointSummary:
    return CheckpointSummary(
        rounds=int(values.get("rounds") or 0),
        plan_chars=len(values.get("plan_markdown") or ""),
        tool_calls=len(values.get("tool_calls") or []),
        todos_count=len(values.get("todos") or []),
        artifact_ready=bool(values.get("artifact")),
    )


def to_checkpoint_read(snapshot: Any, run: Run) -> CheckpointRead:
    """StateSnapshot → 对外 DTO;缺失字段按保守默认值。"""
    metadata = getattr(snapshot, "metadata", None) or {}
    created_at = _parse_created_at(getattr(snapshot, "created_at", None))
    step = metadata.get("step")
    next_nodes = list(getattr(snapshot, "next", ()) or ())
    return CheckpointRead(
        checkpoint_id=str(checkpoint_id_of(snapshot) or ""),
        step=int(step) if isinstance(step, int) else None,
        source=metadata.get("source"),
        next=next_nodes,
        created_at=created_at or run.updated_at or datetime.now(UTC),
        parent_checkpoint_id=parent_checkpoint_id_of(snapshot),
        runnable=bool(next_nodes),
        summary=_summary(dict(getattr(snapshot, "values", None) or {})),
    )


async def list_checkpoints(
    session: AsyncSession,
    graph: CompiledStateGraph,
    account_id: str,
    run_id: str,
    limit: int = 50,
) -> list[CheckpointRead]:
    """账号内 run 的 checkpoint 列表(最新在前)。

    归属先由 require_run 保证(跨账号 404,不泄漏存在性);thread 由 run 派生,
    调用方无法越权读别的线程。图尚未跑过时 history 为空,返回空列表。
    """
    run = await require_run(session, account_id, run_id)
    items: list[CheckpointRead] = []
    async for snapshot in graph.aget_state_history(thread_config(run), limit=limit):
        items.append(to_checkpoint_read(snapshot, run))
    return items


async def find_checkpoint(
    graph: CompiledStateGraph, run: Run, checkpoint_id: str
) -> Any | None:
    """在线程 history 里按 id 找 checkpoint;找不到返回 None(调用方判 409)。"""
    async for snapshot in graph.aget_state_history(thread_config(run)):
        if checkpoint_id_of(snapshot) == checkpoint_id:
            return snapshot
    return None
