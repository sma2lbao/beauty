"""R-24 迁移 smoke:head 唯一、空库 upgrade 后模型与迁移一致。

不连 Postgres、零网络:临时 aiosqlite 文件库上跑真实 alembic 链路(含 env.py)。
P0-05 额外覆盖:沙箱 scope_key 迁移带可执行 downgrade,部分唯一索引在 SQLite 成立。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from common.alembic_kit import check, heads, make_config, upgrade

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


def test_migrations_downgrade_to_base_and_back(tmp_path: Path) -> None:
    """新增迁移必须带可执行的 downgrade(设计 §13.6 DoD)。"""
    settings = _settings(tmp_path)
    config = make_config(MIGRATIONS, settings)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    check(MIGRATIONS, settings)


def test_sandbox_scope_partial_unique_index(tmp_path: Path) -> None:
    """scope_key 唯一索引只约束非空值(SQLite 部分唯一索引)且在迁移后存在。"""
    settings = _settings(tmp_path)
    upgrade(MIGRATIONS, settings)
    with sqlite3.connect(tmp_path / "migrations.db") as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'uq_sandboxes_tenant_scope'"
        ).fetchone()
    assert row is not None
    sql = row[0] or ""
    assert "UNIQUE INDEX uq_sandboxes_tenant_scope" in sql
    assert "WHERE scope_key IS NOT NULL" in sql
