"""维护命令:保留期清理。

用法(服务目录内):
    uv run python -m eval_service.maintenance purge --days 90 --dry-run
    uv run python -m eval_service.maintenance purge --days 30

默认读 EVAL__DATABASE_URL / EVAL__ARTIFACT_STORE 等配置；命令自己开事务，
不经过 HTTP 服务，可放进定时任务/CronJob。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from common.artifacts import ArtifactStore
from database.session import create_session_factory

from eval_service.core.config import Settings
from eval_service.core.storage import build_store
from eval_service.services.retention import purge_expired


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eval-service-maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    purge = sub.add_parser("purge", help="删除超过保留期的运行及其报告对象")
    purge.add_argument(
        "--days",
        type=int,
        default=None,
        help="保留天数;默认读 EVAL__RETENTION_DAYS(0 表示不清理)",
    )
    purge.add_argument("--limit", type=int, default=100, help="每批删除数量")
    purge.add_argument("--dry-run", action="store_true", help="只统计,不删除")
    return parser


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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings()
    days = args.days if args.days is not None else settings.retention_days
    if days <= 0:
        print("保留期未开启(EVAL__RETENTION_DAYS=0)，跳过清理")
        return 0
    removed = asyncio.run(
        _purge(settings, days=days, limit=args.limit, dry_run=args.dry_run)
    )
    action = "命中" if args.dry_run else "删除"
    print(f"{action} {removed} 个超过 {days} 天的评测运行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
