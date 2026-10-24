"""R-24 迁移 smoke:head 唯一、空库 upgrade 后模型与迁移一致。

不连 Postgres、零网络:临时 aiosqlite 文件库上跑真实 alembic 链路(含 env.py)。
"""

from __future__ import annotations

from pathlib import Path

from common.alembic_kit import check, heads, upgrade

from workspace.core.config import Settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'migrations.db'}",
        auto_create_tables=False,
    )


def test_migrations_upgrade_and_check(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert len(heads(MIGRATIONS, settings)) == 1
    upgrade(MIGRATIONS, settings)
    check(MIGRATIONS, settings)
