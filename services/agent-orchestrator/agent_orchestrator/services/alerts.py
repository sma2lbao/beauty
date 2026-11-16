"""运维告警:阈值规则评估 + 事件落库(见 docs/design/alerting.md)。

为什么不复用 `/internal/metrics` 做判定:指标要有人抓取才有意义,而当前部署
没有 Prometheus/Alertmanager(hybrid 部署只跑服务本体)。这里把判定放进进程内的
后台 sweeper,**规则是唯一真源**,出口是三层:结构化 warning 日志、Sentry
(可选 DSN)、`alerts` 表 + `GET /internal/alerts`。

设计要点:

- 规则只读本服务库(`runs`/`jobs`/`outbox_events`),零网络、零业务写入;
- `alerts` 表是 append-only 事件流,当前状态由"每 code 最新一行"给出;
- 只在状态切换时写事件(firing → 不重复写;firing → 消失写 resolved),
  并用 `(code, status, bucket)` 唯一约束收敛多副本的并发写入;
- 评估失败(库暂不可用)只记日志并等下一轮,绝不影响业务路径。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from common.sentry import capture_alert
from database.session import session_scope
from events.outbox import FAILED, PENDING, OutboxEvent
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator import metrics
from agent_orchestrator.models.alert import (
    FIRING,
    RESOLVED,
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    Alert,
)
from agent_orchestrator.models.runs import Job, Run

if TYPE_CHECKING:
    from agent_orchestrator.core.config import Settings
    from agent_orchestrator.core.container import AppContainer

logger = structlog.get_logger(__name__)

#: 判定用的状态字面量(与 RunStatus / JobRepository 的写入值一致)。
_JOB_FAILED = "failed"
_RUN_RUNNING = "running"
_RUN_TERMINAL = ("completed", "failed")
#: 快照里最多回看多少条 resolved 事件。
_SNAPSHOT_RESOLVED_LIMIT = 20


@dataclass(frozen=True, slots=True)
class AlertSignal:
    """一个已被判定为"触发中"的规则结果(尚未落库)。"""

    code: str
    severity: str
    title: str
    summary: str
    value: float
    threshold: float


async def _count(session: AsyncSession, statement: Any) -> int:
    """执行 count 语句;空结果为 0(count 恒返回一行)。"""
    return int(await session.scalar(statement) or 0)


async def evaluate(
    session: AsyncSession, settings: Settings, now: datetime
) -> list[AlertSignal]:
    """按当前库内状态评估全部规则,返回"触发中"的告警(只读)。"""
    signals: list[AlertSignal] = []

    # ① outbox 投递失败:超过重试上限落 failed,必须人工重放或排查消费者。
    failed_events = await _count(
        session,
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.status == FAILED),
    )
    if failed_events > 0:
        signals.append(
            AlertSignal(
                code="outbox_failed",
                severity=SEVERITY_CRITICAL,
                title="outbox 有投递失败的事件",
                summary=(
                    f"失败 {failed_events} 条(阈值 > 0):"
                    "到 /outbox 重放或排查消费者;失败事件不会自动重试"
                ),
                value=float(failed_events),
                threshold=0.0,
            )
        )

    # ② outbox 积压:还在 pending 但可用时间早于"backlog 秒之前"——
    # dispatcher 停摆/消费者长期不可用的信号(尚未到 failed,还有救)。
    backlog_cutoff = now - timedelta(seconds=settings.alerts_outbox_backlog_seconds)
    backlog = await _count(
        session,
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.status == PENDING,
            OutboxEvent.available_at <= backlog_cutoff,
        ),
    )
    if backlog > 0:
        signals.append(
            AlertSignal(
                code="outbox_backlog",
                severity=SEVERITY_WARNING,
                title="outbox 有长期未投递的事件",
                summary=(
                    f"积压 {backlog} 条超过 "
                    f"{settings.alerts_outbox_backlog_seconds} 秒(阈值 > 0):"
                    "检查 dispatcher 是否在跑、消费者是否可用"
                ),
                value=float(backlog),
                threshold=0.0,
            )
        )

    # ③ run 卡死:执行中却长时间没有状态更新(worker 崩溃/进程被杀)。
    stuck_cutoff = now - timedelta(seconds=settings.alerts_run_stuck_seconds)
    stuck = await _count(
        session,
        select(func.count())
        .select_from(Run)
        .where(Run.status == _RUN_RUNNING, Run.updated_at <= stuck_cutoff),
    )
    if stuck > 0:
        signals.append(
            AlertSignal(
                code="run_stuck",
                severity=SEVERITY_CRITICAL,
                title="有 run 卡在 running",
                summary=(
                    f"{stuck} 个 run 超过 {settings.alerts_run_stuck_seconds} 秒"
                    "没有状态更新(阈值 > 0):看 worker 是否在跑,必要时 cancel 后重试"
                ),
                value=float(stuck),
                threshold=0.0,
            )
        )

    # ④ 失败率:窗口内终态达到最小样本量后,失败占比过高说明上游/工具面异常。
    window_start = now - timedelta(seconds=settings.alerts_failure_window_seconds)
    terminal = await _count(
        session,
        select(func.count())
        .select_from(Run)
        .where(Run.status.in_(_RUN_TERMINAL), Run.updated_at >= window_start),
    )
    if terminal >= settings.alerts_failure_min_samples:
        failed_runs = await _count(
            session,
            select(func.count())
            .select_from(Run)
            .where(
                Run.status == _RUN_TERMINAL[1],
                Run.updated_at >= window_start,
            ),
        )
        rate = failed_runs / terminal
        if rate >= settings.alerts_failure_rate:
            signals.append(
                AlertSignal(
                    code="run_failure_rate",
                    severity=SEVERITY_WARNING,
                    title="run 失败率偏高",
                    summary=(
                        f"最近 {settings.alerts_failure_window_seconds} 秒"
                        f" {failed_runs}/{terminal} 失败"
                        f"(阈值 ≥ {settings.alerts_failure_rate:.0%},"
                        f"样本 ≥ {settings.alerts_failure_min_samples})"
                    ),
                    value=rate,
                    threshold=settings.alerts_failure_rate,
                )
            )

    # ⑤ 后台 job 连续失败:重试耗尽落 failed 的行,常见于上游网关/沙箱故障。
    failed_jobs = await _count(
        session,
        select(func.count())
        .select_from(Job)
        .where(Job.status == _JOB_FAILED, Job.updated_at >= window_start),
    )
    if failed_jobs >= settings.alerts_job_failure_threshold:
        signals.append(
            AlertSignal(
                code="job_failures",
                severity=SEVERITY_WARNING,
                title="后台 job 连续失败",
                summary=(
                    f"最近 {settings.alerts_failure_window_seconds} 秒"
                    f"失败 {failed_jobs} 个(阈值 ≥ "
                    f"{settings.alerts_job_failure_threshold}):看 job 的 last_error"
                ),
                value=float(failed_jobs),
                threshold=float(settings.alerts_job_failure_threshold),
            )
        )

    return signals


async def current_states(session: AsyncSession) -> dict[str, str]:
    """每个规则的当前状态:取每个 code 的最新一行(code → firing/resolved)。"""
    ranked = select(
        Alert.code.label("code"),
        Alert.status.label("status"),
        func.row_number()
        .over(partition_by=Alert.code, order_by=Alert.created_at.desc())
        .label("position"),
    ).subquery()
    rows = await session.execute(
        select(ranked.c.code, ranked.c.status).where(ranked.c.position == 1)
    )
    return {code: status for code, status in rows}


def _bucket(now: datetime, settings: Settings) -> int:
    """去重桶序号:同一桶内的重复写入会被唯一约束挡掉。"""
    width = max(1, int(settings.alerts_cooldown_seconds))
    return int(now.timestamp()) // width


async def sweep_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """评估一轮并落事件,返回 {"fired": n, "resolved": n, "purged": n}(评估失败向上抛)。

    写入在同一事务里完成;多副本同时评估时由唯一约束收敛,冲突的副本放弃本轮
    写入并记一条 debug 日志——**判定本身已经在各自进程里重算过**,不会漏报。
    """
    moment = now or datetime.now(UTC)
    bucket = _bucket(moment, settings)
    async with session_scope(session_factory) as session:
        signals = await evaluate(session, settings, moment)
        states = await current_states(session)
        by_code = {signal.code: signal for signal in signals}

        fired: list[AlertSignal] = [
            signal for signal in signals if states.get(signal.code) != FIRING
        ]
        resolved_codes = [
            code
            for code, status in states.items()
            if status == FIRING and code not in by_code
        ]
        purged = 0
        # 必须整段包进 try:保留期 delete 是查询,会先把待写的 add 自动 flush 出去,
        # 唯一约束冲突就在那里抛,不能等 commit 才兜。
        try:
            for signal in fired:
                session.add(
                    Alert(
                        code=signal.code,
                        severity=signal.severity,
                        status=FIRING,
                        title=signal.title,
                        summary=signal.summary,
                        value=signal.value,
                        threshold=signal.threshold,
                        bucket=bucket,
                    )
                )
            for code in resolved_codes:
                session.add(
                    Alert(
                        code=code,
                        severity=SEVERITY_WARNING,
                        status=RESOLVED,
                        title="告警恢复",
                        summary=f"{code} 本轮评估不再触发",
                        bucket=bucket,
                    )
                )
            # 保留期:只清已恢复的历史,firing 是待办,不自动删。
            cutoff = moment - timedelta(days=settings.alerts_retention_days)
            purged = (
                await session.execute(
                    delete(Alert).where(
                        Alert.status == RESOLVED,
                        Alert.created_at < cutoff,
                    )
                )
            ).rowcount or 0
            await session.commit()
        except IntegrityError:
            # 另一副本在同一桶里先写了:放弃本轮写入,下轮会读到它的状态。
            await session.rollback()
            logger.debug("alert sweep lost dedupe race", bucket=bucket)
            return {"fired": 0, "resolved": 0, "purged": 0}

    for signal in fired:
        logger.warning(
            "alert firing",
            code=signal.code,
            severity=signal.severity,
            value=signal.value,
            threshold=signal.threshold,
            summary=signal.summary,
        )
        metrics.observe_alert_fired(code=signal.code, severity=signal.severity)
        capture_alert(
            code=signal.code, summary=signal.summary, severity=signal.severity
        )
    for code in resolved_codes:
        logger.info("alert resolved", code=code)
        metrics.observe_alert_resolved(code=code)
    if fired or resolved_codes:
        logger.info(
            "alert sweep completed",
            fired=len(fired),
            resolved=len(resolved_codes),
            purged=purged,
        )
    return {
        "fired": len(fired),
        "resolved": len(resolved_codes),
        "purged": int(purged),
    }


async def snapshot(session: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    """当前告警视图:firing 列表 + 最近恢复的历史(端点用)。"""
    ranked = select(
        Alert.id.label("id"),
        func.row_number()
        .over(partition_by=Alert.code, order_by=Alert.created_at.desc())
        .label("position"),
    ).subquery()
    latest_ids = select(ranked.c.id).where(ranked.c.position == 1)
    latest = (
        (
            await session.execute(
                select(Alert)
                .where(Alert.id.in_(latest_ids))
                .order_by(Alert.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    resolved = (
        (
            await session.execute(
                select(Alert)
                .where(Alert.status == RESOLVED)
                .order_by(Alert.created_at.desc())
                .limit(_SNAPSHOT_RESOLVED_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    return {
        "firing": [
            _as_dict(row, including_resolved_at=False)
            for row in latest
            if row.status == FIRING
        ],
        "resolved": [_as_dict(row, including_resolved_at=True) for row in resolved],
    }


def _as_dict(row: Alert, *, including_resolved_at: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": row.code,
        "severity": row.severity,
        "title": row.title,
        "summary": row.summary,
        "value": row.value,
        "threshold": row.threshold,
    }
    if including_resolved_at:
        payload["resolved_at"] = row.created_at.isoformat()
    else:
        payload["since"] = row.created_at.isoformat()
    return payload


async def sweeper_loop(container: AppContainer) -> None:
    """周期评估;间隔 <= 0 或没有数据库时直接退出(不算错误)。"""
    interval = float(container.settings.alerts_sweep_interval_seconds)
    if interval <= 0 or container.session_factory is None:
        return
    settings = container.settings
    while True:
        try:
            await sweep_once(container.session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 下一轮继续
            logger.warning("alert sweep iteration failed", error=str(exc))
        await asyncio.sleep(interval)


def start_sweeper(container: AppContainer) -> asyncio.Task[None]:
    """作为后台任务启动;调用方(容器)负责在 stop 时取消。"""
    return asyncio.create_task(
        sweeper_loop(container), name="orchestrator-alert-sweeper"
    )
