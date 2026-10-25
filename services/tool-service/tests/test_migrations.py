"""R-24 迁移 smoke:head 唯一、空库 upgrade 后模型与迁移一致。

零网络:临时 aiosqlite 文件库上跑真实 alembic 链路(含 env.py 从
TOOL__DATABASE_URL 注入连接)。额外断言 tool_calls 的唯一约束与索引
在迁移后真实存在,防止「测试 create_all 有、真实库缺迁移」。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from common.alembic_kit import check, heads, make_config, upgrade

from tool_service.core.config import Settings

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


def test_tool_calls_unique_constraint_and_indexes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade(MIGRATIONS, settings)
    with sqlite3.connect(tmp_path / "migrations.db") as connection:
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'tool_calls'"
            ).fetchall()
        }
        # SQLite 把表级 UNIQUE 落成 autoindex,名字由引擎生成;
        # 这里按「唯一 + 列组合」断言,PG 上则沿用命名约束 uq_tool_calls_tenant_call。
        unique_columns = []
        for row in connection.execute("PRAGMA index_list(tool_calls)").fetchall():
            if not row[2]:
                continue
            columns_in_index = [
                info[2]
                for info in connection.execute(
                    f"PRAGMA index_info({row[1]})"
                ).fetchall()
            ]
            if columns_in_index == ["tenant_id", "tool_call_id"]:
                unique_columns = columns_in_index
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tool_calls)").fetchall()
        }
    assert unique_columns == ["tenant_id", "tool_call_id"]
    assert "ix_tool_calls_run" in indexes
    assert "ix_tool_calls_tool" in indexes
    assert {"tool_call_id", "tenant_id", "tool_name", "arguments_sha256"} <= columns
