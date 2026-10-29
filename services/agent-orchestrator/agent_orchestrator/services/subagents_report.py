"""子代理收尾:父 run 过程项落库、摘要抽取与回喂父模型的 JSON(自 subagents.py 拆出)。

状态映射见契约 §6:待人工(awaiting_*)立即返回不阻塞父 run;completed 带
摘要与用量;failed/blocked/cancelled 落失败项,``data.reason`` 用状态值。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from common.errors import NotFoundError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph import items
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import RunEventRepository
from agent_orchestrator.schemas.runs import RunStatus, RunUsageTotals
from agent_orchestrator.services import usage_events
from agent_orchestrator.services.items import append_item_event, list_items
from agent_orchestrator.services.run_artifacts import get_artifact
from agent_orchestrator.services.subagents_policy import _Request
from agent_orchestrator.services.subagents_runtime import _cancel_text
from agent_orchestrator.services.subagents_spec import (
    _ABORT_TIMEOUT,
    _AWAITING_STATUSES,
)


async def _finish_aborted(
    session: AsyncSession,
    parent: Run,
    child: Run | None,
    *,
    thread_id: str,
    item_id: str,
    request: _Request,
    base_data: dict[str, Any],
    started_at: datetime,
    abort: str,
) -> dict[str, Any]:
    """超时/取消收尾:父 item failed,返回 ok=false。"""

    reason = "timeout" if abort == _ABORT_TIMEOUT else "cancelled"
    data = {
        **base_data,
        "child_status": RunStatus.CANCELLED.value,
        "summary": "",
        "reason": reason,
    }
    await append_item_event(
        session,
        parent,
        thread_id=thread_id,
        item_id=item_id,
        kind=items.KIND_SUBAGENT,
        phase=items.PHASE_FAILED,
        title=request.title,
        data=data,
        node="execute",
        status=items.STATUS_FAILED,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )
    await session.commit()
    return {
        "ok": False,
        "error": f"subagent.{reason}",
        "status": RunStatus.CANCELLED.value,
        "detail": _cancel_text(abort),
        "subagent_run_id": child.id if child is not None else None,
    }


async def _finish(
    session: AsyncSession,
    parent: Run,
    child: Run,
    *,
    thread_id: str,
    item_id: str,
    title: str,
    base_data: dict[str, Any],
    started_at: datetime,
    store: Any,
    settings: Settings,
) -> dict[str, Any]:
    """按子 run 终态落父 item 并组装回喂父模型的 JSON(§6 状态映射)。"""

    status = child.status
    usage = await usage_events.usage_totals(session, parent.tenant_id, child.id)
    usage_data = usage.model_dump()

    if status in _AWAITING_STATUSES:
        # 待人工:立即返回,不阻塞父 run;人工处理后可续跑子 run。
        await _append_finish_item(
            session,
            parent,
            thread_id=thread_id,
            item_id=item_id,
            title=title,
            data={
                **base_data,
                "child_status": status,
                "summary": "",
                "usage": usage_data,
            },
            phase=items.PHASE_COMPLETED,
            status=items.STATUS_COMPLETED,
            started_at=started_at,
        )
        await session.commit()
        return {
            "ok": True,
            "subagent_run_id": child.id,
            "status": status,
            "summary": "",
            "note": "子代理等待人工审批,可在运行详情继续",
            "usage": _usage_reply(usage),
        }

    summary = await _child_summary(session, child, store=store, settings=settings)

    if status == RunStatus.COMPLETED.value:
        await _append_finish_item(
            session,
            parent,
            thread_id=thread_id,
            item_id=item_id,
            title=title,
            data={
                **base_data,
                "child_status": status,
                "summary": summary,
                "usage": usage_data,
            },
            phase=items.PHASE_COMPLETED,
            status=items.STATUS_COMPLETED,
            started_at=started_at,
        )
        await session.commit()
        return {
            "ok": True,
            "subagent_run_id": child.id,
            "status": status,
            "summary": summary,
            "usage": _usage_reply(usage),
        }

    # failed / blocked / cancelled:父 item 落失败项,data.reason 用状态值。
    await _append_finish_item(
        session,
        parent,
        thread_id=thread_id,
        item_id=item_id,
        title=title,
        data={
            **base_data,
            "child_status": status,
            "summary": summary,
            "usage": usage_data,
            "reason": status,
        },
        phase=items.PHASE_FAILED,
        status=items.STATUS_FAILED,
        started_at=started_at,
    )
    await session.commit()
    return {
        "ok": False,
        "error": f"subagent.{status}",
        "status": status,
        "detail": child.status_reason or summary or f"子代理状态:{status}",
        "subagent_run_id": child.id,
        "usage": _usage_reply(usage),
    }


async def _append_finish_item(
    session: AsyncSession,
    parent: Run,
    *,
    thread_id: str,
    item_id: str,
    title: str,
    data: dict[str, Any],
    phase: str,
    status: str,
    started_at: datetime,
) -> None:
    await append_item_event(
        session,
        parent,
        thread_id=thread_id,
        item_id=item_id,
        kind=items.KIND_SUBAGENT,
        phase=phase,
        title=title,
        data=data,
        node="execute",
        status=status,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


async def _child_summary(
    session: AsyncSession,
    child: Run,
    *,
    store: Any,
    settings: Settings,
) -> str:
    """子 run 结论摘要:交付物正文 → run.completed.v1.results → status_reason。"""

    text = ""
    try:
        view = await get_artifact(session, child.tenant_id, child.id, store)
        text = view.content or ""
    except NotFoundError:
        text = ""
    if not text:
        events = await RunEventRepository(session).list_for_run(
            child.tenant_id, child.id
        )
        for event in reversed(events):
            if event.type != "run.completed.v1":
                continue
            results = (event.payload or {}).get("results") or []
            text = "\n".join(str(item) for item in results)
            break
    if not text:
        text = child.status_reason or ""
    return text[: settings.subagent_summary_max_chars]


async def _item_summaries(
    session: AsyncSession, tenant_id: str, parent_id: str
) -> dict[str, str]:
    """父 run 里每个子代理 item 最近一次完成摘要(列表页 summary 列)。"""

    summaries: dict[str, str] = {}
    for item in await list_items(session, tenant_id, parent_id):
        if item.kind != items.KIND_SUBAGENT:
            continue
        child_id = item.data.get("child_run_id")
        summary = item.data.get("summary")
        if isinstance(child_id, str) and isinstance(summary, str):
            summaries[child_id] = summary
    return summaries


def _usage_reply(usage: RunUsageTotals) -> dict[str, int]:
    return {"total_tokens": usage.total_tokens, "calls": usage.calls}
