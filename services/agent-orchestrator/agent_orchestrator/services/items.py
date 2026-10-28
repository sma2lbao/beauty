"""过程项视图(P1-D):item_id/thread_id 的唯一实现 + ``run.item.v1`` 投影。

零迁移:item 身份与各相位数据都固化在 append-only 的事件 payload 里
(``run_events`` 是审计流水),这里只读投影;写入侧由 ``graph/items.py`` 采集、
``services/run_events.py`` 在图调用边界统一落库。

契约见 ``docs/design/event-taxonomy.md`` §2/§6。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.graph import items, steps
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import RunEventRepository
from agent_orchestrator.schemas.runs import RunItemRead
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_lifecycle import require_run

#: 过程项事件类型(对外投影与回放都以它为准)。
ITEM_EVENT_TYPE = items.EVENT_TYPE

#: thread 解析沿 ``parent_run_id`` 上溯的深度上限(防环,§2)。
MAX_THREAD_DEPTH = 8

#: 计划正文上限(字符):超出截断并在 data.truncated 标真。
MAX_PLAN_CHARS = 20000


async def resolve_thread_id(session: AsyncSession, run: Run) -> str:
    """thread_id 解析(§2):有会话用会话 id,否则沿父 run 上溯到根。

    子 run / retry 与父 run 同 thread;链深上限 :data:`MAX_THREAD_DEPTH`,
    遇到环或缺失父 run 时停在当前节点——解析失败不能让 run 起不来。
    """

    if run.conversation_id:
        return str(run.conversation_id)
    current: Run = run
    seen: set[str] = {run.id}
    depth = 0
    while current.parent_run_id and depth < MAX_THREAD_DEPTH:
        parent_id = str(current.parent_run_id)
        if parent_id in seen:
            break
        parent = await session.get(Run, parent_id)
        if parent is None:
            break
        seen.add(parent.id)
        current = parent
        depth += 1
    return f"run:{current.id}"


def plan_content(markdown: str | None) -> tuple[str, int, bool]:
    """计划正文的落库形态:截断到上限,返回 (content, chars, truncated)。"""

    content = markdown or ""
    truncated = len(content) > MAX_PLAN_CHARS
    if truncated:
        content = content[:MAX_PLAN_CHARS]
    return content, len(content), truncated


async def append_item_event(
    session: AsyncSession,
    run: Run,
    *,
    thread_id: str,
    item_id: str,
    kind: str,
    phase: str,
    title: str,
    data: dict[str, Any] | None = None,
    attempt: int | None = None,
    node: str | None = None,
    step_id: str | None = None,
    status: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    """落一条 ``run.item.v1``(展示事件,不进 outbox;§3)。"""

    moment = started_at or datetime.now(UTC)
    payload: dict[str, Any] = {
        "item_id": item_id,
        "thread_id": thread_id,
        "turn_id": run.id,
        "kind": kind,
        "phase": phase,
        "node": node,
        "step_id": step_id,
        "attempt": run.attempts if attempt is None else attempt,
        "title": title,
        "data": dict(data or {}),
        "started_at": moment.isoformat(),
    }
    if status is not None:
        payload["status"] = status
    if finished_at is not None:
        payload["finished_at"] = finished_at.isoformat()
        payload["duration_ms"] = int((finished_at - moment).total_seconds() * 1000)
    await append_event(session, run, ITEM_EVENT_TYPE, payload, record_outbox=False)


async def _events(session: AsyncSession, run: Run) -> list[Any]:
    return await RunEventRepository(session).list_for_run(run.tenant_id, run.id)


def _item_number(item_id: str) -> int | None:
    suffix = item_id.rsplit(":", 1)[-1]
    return int(suffix) if suffix.isdigit() else None


async def existing_item_numbers(
    session: AsyncSession, run: Run, kind: str, *, attempt: int | None = None
) -> set[int]:
    """该 run 指定 (attempt, kind) 已用掉的 item 序号(plan 版本号另算,跳过)。"""

    target_attempt = run.attempts if attempt is None else attempt
    numbers: set[int] = set()
    for event in await _events(session, run):
        if event.type != ITEM_EVENT_TYPE:
            continue
        payload = event.payload or {}
        if payload.get("kind") != kind:
            continue
        if int(payload.get("attempt") or 0) != target_attempt:
            continue
        item_id = str(payload.get("item_id") or "")
        if ":plan:" in item_id:
            continue
        number = _item_number(item_id)
        if number is not None:
            numbers.add(number)
    return numbers


async def next_item_id(session: AsyncSession, run: Run, kind: str) -> str:
    """按 ``<run_id>:<attempt>:<kind>:<n>`` 分配下一个 item_id。"""

    numbers = await existing_item_numbers(session, run, kind)
    return f"{run.id}:{run.attempts}:{kind}:{max(numbers, default=0) + 1}"


async def next_plan_version(session: AsyncSession, run: Run) -> tuple[int, str | None]:
    """plan 版本化(§4):返回 (version, supersedes 上一版 item_id)。

    version = 已到终态(completed/failed)的 plan item 数 + 1——失败的版本也占号,
    否则重试会复用同一个 item_id、把上次失败相位并进新版本;跨 attempt 连续。
    supersedes 指向最新一版**成功**的计划(无历史版本为 None)。
    """

    completed: list[dict[str, Any]] = []
    terminal = 0
    for event in await _events(session, run):
        if event.type != ITEM_EVENT_TYPE:
            continue
        payload = event.payload or {}
        if payload.get("kind") != items.KIND_PLAN:
            continue
        if payload.get("phase") not in {items.PHASE_COMPLETED, items.PHASE_FAILED}:
            continue
        terminal += 1
        if payload.get("phase") == items.PHASE_COMPLETED:
            completed.append(payload)
    supersedes = None
    if completed:
        supersedes = str(completed[-1].get("item_id") or "") or None
    return terminal + 1, supersedes


async def latest_step_id(session: AsyncSession, run: Run, node: str) -> str | None:
    """最近一个指定节点的 step_id(plan item 尽力关联;取不到为 None)。"""

    found: str | None = None
    for event in await _events(session, run):
        if event.type != steps.EVENT_TYPE:
            continue
        payload = event.payload or {}
        if payload.get("node") != node:
            continue
        step_id = payload.get("step_id")
        if step_id:
            found = str(step_id)
    return found


def _status_of(phase: str, raw_status: Any) -> str:
    """投影状态(§6):有 status 用原值;无 status 时按相位推导。"""

    if raw_status:
        return str(raw_status)
    if phase in {items.PHASE_COMPLETED, items.PHASE_FAILED}:
        return phase
    return "running"


async def list_items(
    session: AsyncSession, tenant_id: str, run_id: str
) -> list[RunItemRead]:
    """把 ``run.item.v1`` 事件投影成「每项一条」的视图(§6)。

    按 seq 升序遍历:item_id 首次出现决定顺序与静态字段;phase/status/
    finished_at/duration_ms 取最新相位;data 按 key 浅合并(后写覆盖先写)。
    无 item 的 run 返回空数组(不是 404)。
    """

    await require_run(session, tenant_id, run_id)
    events = await RunEventRepository(session).list_for_run(tenant_id, run_id)
    order: list[str] = []
    merged: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type != ITEM_EVENT_TYPE:
            continue
        payload = event.payload or {}
        item_id = str(payload.get("item_id") or "")
        if not item_id:
            continue
        phase = str(payload.get("phase") or "")
        entry = merged.get(item_id)
        if entry is None:
            order.append(item_id)
            entry = {
                "item_id": item_id,
                "thread_id": str(payload.get("thread_id") or ""),
                "turn_id": str(payload.get("turn_id") or ""),
                "kind": str(payload.get("kind") or items.KIND_TOOL),
                "phase": phase or items.PHASE_COMPLETED,
                "status": _status_of(phase, payload.get("status")),
                "node": payload.get("node"),
                "step_id": payload.get("step_id"),
                "attempt": int(payload.get("attempt") or 0),
                "title": str(payload.get("title") or ""),
                "seq": event.seq,
                "started_at": payload.get("started_at"),
                "finished_at": None,
                "duration_ms": None,
                "data": {},
            }
            merged[item_id] = entry
        if phase:
            entry["phase"] = phase
        status = payload.get("status")
        if status:
            entry["status"] = str(status)
        elif phase in {items.PHASE_COMPLETED, items.PHASE_FAILED}:
            entry["status"] = phase
        elif phase in {items.PHASE_STARTED, items.PHASE_DELTA}:
            entry["status"] = "running"
        if payload.get("step_id") and not entry.get("step_id"):
            # plan 的 started 相位还拿不到 step_id(步骤在图里尚未结束),
            # 由后续相位补全——否则投影永远停在 first 的 None。
            entry["step_id"] = str(payload["step_id"])
        if payload.get("node") and not entry.get("node"):
            entry["node"] = payload["node"]
        if payload.get("started_at"):
            entry["started_at"] = payload["started_at"]
        if payload.get("finished_at"):
            entry["finished_at"] = payload["finished_at"]
        if payload.get("duration_ms") is not None:
            entry["duration_ms"] = payload["duration_ms"]
        data = payload.get("data")
        if isinstance(data, dict):
            entry["data"].update(data)
    return [RunItemRead(**merged[item_id]) for item_id in order]
