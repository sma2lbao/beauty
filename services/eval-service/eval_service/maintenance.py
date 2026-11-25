"""维护命令:保留期清理、常驻清理循环与 COS 桶生命周期。

用法(服务目录内):
    uv run python -m eval_service.maintenance purge --days 90 --dry-run
    uv run python -m eval_service.maintenance purge --days 30
    uv run python -m eval_service.maintenance loop --once
    uv run python -m eval_service.maintenance loop
    uv run python -m eval_service.maintenance lifecycle --days 180
    uv run python -m eval_service.maintenance lifecycle --days 180 --apply

默认读 EVAL__DATABASE_URL / EVAL__ARTIFACT_STORE 等配置；命令自己开事务，
不经过 HTTP 服务，可放进定时任务/CronJob。loop 是常驻进程，compose 的
eval-retention 用它；--once 供宿主机 cron 使用。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

from common.artifacts import ArtifactStore
from common.logging import get_logger
from database.session import create_session_factory

from eval_service.core.config import Settings
from eval_service.core.storage import build_store
from eval_service.services.lifecycle import apply_lifecycle
from eval_service.services.retention import purge_expired

log = get_logger("eval.retention")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eval-service-maintenance")
    sub = parser.add_subparsers(dest="command", required=True)

    purge = sub.add_parser("purge", help="删除超过保留期的运行及其报告对象")
    _add_retention_args(purge)

    loop = sub.add_parser("loop", help="常驻清理循环；--once 只跑一轮")
    _add_retention_args(loop)
    loop.add_argument(
        "--interval",
        type=int,
        default=None,
        help="循环间隔秒数；默认读 EVAL__RETENTION_INTERVAL_SECONDS(86400)",
    )
    loop.add_argument("--once", action="store_true", help="只清理一轮后退出")

    lifecycle = sub.add_parser("lifecycle", help="打印/应用 COS 报告对象生命周期策略")
    lifecycle.add_argument(
        "--days",
        type=int,
        default=None,
        help="对象过期天数；默认读 EVAL__ARTIFACT_LIFECYCLE_DAYS(180)",
    )
    lifecycle.add_argument(
        "--prefix",
        default=None,
        help="桶内前缀；默认读 EVAL__ARTIFACT_LIFECYCLE_PREFIX(eval-runs/)",
    )
    lifecycle.add_argument(
        "--apply",
        action="store_true",
        help="真正写入 COS；不带时只打印策略（dry-run）",
    )
    return parser


def _add_retention_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="保留天数;默认读 EVAL__RETENTION_DAYS(0 表示不清理)",
    )
    parser.add_argument("--limit", type=int, default=100, help="每批删除数量")


async def _purge(settings: Settings, *, days: int, limit: int, dry_run: bool) -> int:
    before = datetime.now(UTC) - timedelta(days=days)
    store: ArtifactStore | None = build_store(settings)
    factory = create_session_factory(settings.database_url)
    try:
        async with factory() as session:
            removed = await purge_expired(
                session,
                store,
                before=before,
                limit=limit,
                dry_run=dry_run,
            )
            if not dry_run:
                await session.commit()
            return removed
    finally:
        await factory.engine.dispose()


async def _purge_loop(
    settings: Settings,
    *,
    days: int,
    limit: int,
    interval: int,
    once: bool,
) -> int:
    """立即清理一轮，然后按间隔循环；单轮失败不退出常驻进程。"""

    while True:
        try:
            removed = await _purge(settings, days=days, limit=limit, dry_run=False)
            print(
                f"删除 {removed} 个超过 {days} 天的评测运行",
                flush=True,
            )
        except Exception:  # noqa: BLE001 - 定时清理不能因一次失败退出
            log.exception("eval.retention_cycle_failed")
            if once:
                return 1
        if once:
            return 0
        await asyncio.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings()

    if args.command == "lifecycle":
        days = args.days if args.days is not None else settings.artifact_lifecycle_days
        prefix = (
            args.prefix
            if args.prefix is not None
            else settings.artifact_lifecycle_prefix
        )
        try:
            policy = asyncio.run(
                apply_lifecycle(
                    settings,
                    days=days,
                    prefix=prefix,
                    apply=args.apply,
                )
            )
        except (ValueError, RuntimeError) as exc:
            print(f"生命周期策略失败:{exc}", file=sys.stderr)
            return 2
        print(json.dumps(policy, ensure_ascii=False, indent=2))
        if not args.apply:
            print("dry-run:未写入 COS；确认后加 --apply")
        return 0

    days = args.days if args.days is not None else settings.retention_days
    if days <= 0:
        print("保留期未开启(EVAL__RETENTION_DAYS=0)，跳过清理")
        return 0

    if args.command == "loop":
        interval = (
            args.interval
            if args.interval is not None
            else settings.retention_interval_seconds
        )
        if interval < 1:
            print("清理间隔必须大于 0", file=sys.stderr)
            return 2
        return asyncio.run(
            _purge_loop(
                settings,
                days=days,
                limit=args.limit,
                interval=interval,
                once=args.once,
            )
        )

    removed = asyncio.run(
        _purge(settings, days=days, limit=args.limit, dry_run=args.dry_run)
    )
    action = "命中" if args.dry_run else "删除"
    print(f"{action} {removed} 个超过 {days} 天的评测运行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
