"""运行保留期清理:按批删除过期运行及其报告对象。

只由维护命令调用，不在请求线程里跑；删除顺序为子表 → 主表，对象先删，
保证即使中途失败也不会留下“对象还在、DB 已删”的不可达引用。
"""

from __future__ import annotations

from datetime import datetime

from common.artifacts import ArtifactStore
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from eval_service.core.errors import ReportStoreUnavailableError
from eval_service.models import EvalCaseRun, EvalRun, EvalSuiteRun
from eval_service.repositories import EvalRunRepository


async def purge_expired(
    session: AsyncSession,
    store: ArtifactStore | None,
    *,
    before: datetime,
    limit: int = 100,
    dry_run: bool = False,
) -> int:
    """删除 created_at < before 的运行；返回删除（或 dry-run 命中）数量。"""

    repository = EvalRunRepository(session)
    if dry_run:
        return await repository.count_expired(before)

    removed = 0
    while True:
        runs = await repository.list_expired(before, limit=limit)
        if not runs:
            break
        keys = [run.report_key for run in runs if run.report_key]
        if keys:
            if store is None:
                raise ReportStoreUnavailableError(
                    "eval.report_store_unavailable",
                    "存在转存对象存储的报告，但清理命令未装配存储",
                )
            for key in keys:
                await store.delete(key)

        run_ids = [str(run.id) for run in runs]
        await session.execute(
            delete(EvalCaseRun).where(EvalCaseRun.run_id.in_(run_ids))
        )
        await session.execute(
            delete(EvalSuiteRun).where(EvalSuiteRun.run_id.in_(run_ids))
        )
        await session.execute(delete(EvalRun).where(EvalRun.id.in_(run_ids)))
        removed += len(runs)
        if len(runs) < limit:
            break
    await session.flush()
    return removed
