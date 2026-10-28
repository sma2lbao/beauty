"""run 用量事件(P1-E):把采集到的模型用量落成 ``run.usage.v1``。

用量是观测数据,不是执行路径:``_usage_boundary`` 退出时按采集器数据追加
一条事件(``record_outbox=False``,不进 outbox),GET /runs/{id}/usage 与
子代理回喂父 run 的 usage 都从这条事件聚合。无采样则不落事件——空跑不该
在事件流里留噪声。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import RunEvent
from agent_orchestrator.repositories import RunEventRepository, RunRepository
from agent_orchestrator.schemas.runs import (
    RunUsageModelRead,
    RunUsageRead,
    RunUsageTotals,
)
from agent_orchestrator.services.usage import UsageSample

#: 用量事件类型(观测类事件,append-only 零迁移)。
USAGE_EVENT = "run.usage.v1"

_TOTAL_KEYS = ("calls", "prompt_tokens", "completion_tokens", "total_tokens")


def _blank() -> dict[str, int]:
    return {key: 0 for key in _TOTAL_KEYS}


def usage_payload(samples: list[UsageSample]) -> dict[str, Any]:
    """按模型聚合采样值:同一次图调用可能换模型,先分组再合计。"""
    models: dict[str, dict[str, int]] = {}
    for sample in samples:
        bucket = models.setdefault(sample.model or "", _blank())
        bucket["calls"] += 1
        bucket["prompt_tokens"] += sample.prompt_tokens
        bucket["completion_tokens"] += sample.completion_tokens
        bucket["total_tokens"] += sample.total_tokens
    totals = _blank()
    for bucket in models.values():
        for key in _TOTAL_KEYS:
            totals[key] += bucket[key]
    return {**totals, "models": models}


def _merge(totals: RunUsageTotals, payload: dict[str, Any]) -> None:
    totals.calls += int(payload.get("calls", 0) or 0)
    totals.prompt_tokens += int(payload.get("prompt_tokens", 0) or 0)
    totals.completion_tokens += int(payload.get("completion_tokens", 0) or 0)
    totals.total_tokens += int(payload.get("total_tokens", 0) or 0)


async def _usage_events(
    session: AsyncSession, tenant_id: str, run_id: str
) -> list[RunEvent]:
    """该 run 的用量事件(按 seq 升序;其它类型事件不参与聚合)。"""
    events = await RunEventRepository(session).list_for_run(tenant_id, run_id)
    return [event for event in events if event.type == USAGE_EVENT]


async def usage_totals(
    session: AsyncSession, tenant_id: str, run_id: str
) -> RunUsageTotals:
    """聚合某 run 的全部用量事件(无事件时全零,不抛 404)。"""
    totals = RunUsageTotals()
    for event in await _usage_events(session, tenant_id, run_id):
        _merge(totals, event.payload or {})
    return totals


async def run_usage(session: AsyncSession, tenant_id: str, run_id: str) -> RunUsageRead:
    """GET /runs/{id}/usage:先校验 run 归属(不存在 404),再汇总用量。"""
    await RunRepository(session).require_for_tenant(tenant_id, run_id)
    totals = RunUsageTotals()
    by_model: dict[str, RunUsageModelRead] = {}
    for event in await _usage_events(session, tenant_id, run_id):
        payload = event.payload or {}
        _merge(totals, payload)
        for model, bucket in (payload.get("models") or {}).items():
            row = by_model.setdefault(model, RunUsageModelRead(model=model))
            _merge(row, bucket)
    return RunUsageRead(
        run_id=run_id,
        by_model=list(by_model.values()),
        **totals.model_dump(),
    )
