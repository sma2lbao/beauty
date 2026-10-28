#!/usr/bin/env python3
"""本地开发数据库迁移:把 services/* 的 SQLite 库对齐到 alembic head。

为什么需要:scripts/dev.sh 只负责启动服务,不跑迁移;服务内 AUTO_CREATE_TABLES
的 create_all 只建缺失的表,不会给已有表加列。ORM 一加列(如 M4a 的 runs.todos),
旧库就持续 500(no such column: runs.todos)。本脚本在启动服务前把库推到 head。

处理策略(按库现状):
- 空库/库不存在 → alembic upgrade head(直接建到 head);
- 已登记 alembic_version → alembic upgrade head(落后补齐,已最新则跳过);
- 未登记(create_all 建的库)或 revision 无法识别 → 先在临时副本上 stamp head +
  alembic check,确认结构与 head 完全一致才在真实库登记基线(收养);
  有漂移则不动真实库并失败,提示 npm run dev:reset;
- 非 SQLite(如 Postgres)→ 跳过并提示手动 alembic upgrade head。

执行方式:用共享 venv 的 python -m alembic 子进程(工作目录 = 服务目录),不走
uv run——uv run 会按单项目 dev group 精确同步共享 venv,剪掉兄弟项目的 dev 依赖。
数据库地址先读环境变量 `<PREFIX>__DATABASE_URL`,再读服务 .env,最后回退默认
sqlite 路径;相对路径按服务目录解析(与服务运行时 cwd 一致)。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (服务目录, Settings 环境变量前缀, 默认 db 文件名)
SERVICES: tuple[tuple[str, str, str], ...] = (
    ("account-service", "ACCOUNT", "account.db"),
    ("agent-orchestrator", "AGENT_ORCHESTRATOR", "agent_orchestrator.db"),
    ("connector-service", "CONNECTOR", "connector.db"),
    ("knowledge-service", "KNOWLEDGE", "knowledge.db"),
    ("workspace-service", "WORKSPACE", "workspace.db"),
    ("tool-service", "TOOL", "tool.db"),
)


def log(message: str) -> None:
    print(message, flush=True)


def env_file_value(path: Path, key: str) -> str | None:
    """读 .env 里的 KEY=value(忽略注释;去掉包裹引号)。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def database_url(service_dir: Path, prefix: str) -> str | None:
    """环境变量优先(pydantic-settings 同规则),其次服务 .env。"""
    key = f"{prefix}__DATABASE_URL"
    from_env = os.environ.get(key, "").strip()
    if from_env:
        return from_env
    return env_file_value(service_dir / ".env", key)


def sqlite_file(url: str | None, service_dir: Path) -> Path | None:
    """解析 sqlite URL 为库文件绝对路径;非 sqlite 或内存库返回 None。"""
    if not url or not url.startswith("sqlite"):
        return None
    raw = url.split(":///", 1)[-1].split("?", 1)[0].strip() if ":///" in url else ""
    if not raw or raw == ":memory:":
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = service_dir / path
    return path.resolve()


def alembic(
    service_dir: Path, prefix: str, *args: str, url: str | None = None
) -> subprocess.CompletedProcess[str]:
    """在服务目录下执行 python -m alembic;url 用于临时副本等场景覆盖库地址。"""
    env = os.environ.copy()
    if url is not None:
        env[f"{prefix}__DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=service_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def head_revision(service_dir: Path, prefix: str) -> str:
    proc = alembic(service_dir, prefix, "heads")
    first = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not first:
        return "?"
    return first[0].split()[0]


def tracked_revision(db: Path) -> str | None:
    """alembic_version 里的 revision;None = 没有版本表(create_all 建的库)。"""
    if not db.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            has_table = conn.execute(
                "select 1 from sqlite_master "
                "where type='table' and name='alembic_version'"
            ).fetchone()
            if not has_table:
                return None
            row = conn.execute(
                "select version_num from alembic_version limit 1"
            ).fetchone()
            return str(row[0]) if row else ""
    except sqlite3.Error:
        return None


def table_count(db: Path) -> int:
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "select count(*) from sqlite_master where type='table'"
            ).fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def upgrade_head(service_dir: Path, prefix: str) -> subprocess.CompletedProcess[str]:
    """upgrade 到 head;SQLite 被并发写锁住时重试(dev 服务可能还在跑)。"""
    proc = alembic(service_dir, prefix, "upgrade", "head")
    for _ in range(3):
        if proc.returncode == 0:
            return proc
        combined = f"{proc.stdout}\n{proc.stderr}".lower()
        if "database is locked" not in combined:
            return proc
        time.sleep(1)
        proc = alembic(service_dir, prefix, "upgrade", "head")
    return proc


def adopt_untracked(
    service_dir: Path,
    prefix: str,
    db: Path,
    head: str,
) -> tuple[bool, str]:
    """未登记版本的库:副本上 stamp+check,结构与 head 一致才给真实库登记基线。"""
    with tempfile.TemporaryDirectory(prefix="beauty-migrate-") as tmp:
        copy = Path(tmp) / db.name
        try:
            # sqlite backup API 生成一致快照(兼容 WAL 中未合并的数据)
            with (
                sqlite3.connect(f"file:{db}?mode=ro", uri=True) as src,
                sqlite3.connect(copy) as dst,
            ):
                src.backup(dst)
        except sqlite3.Error as exc:
            return False, f"复制库文件失败:{exc}"

        # 副本上清掉不可识别的版本记录:check 以 head 为当前基线比对结构。
        try:
            with sqlite3.connect(copy) as conn:
                conn.execute("drop table if exists alembic_version")
                conn.commit()
        except sqlite3.Error as exc:
            return False, f"清理副本 alembic_version 失败:{exc}"

        copy_url = f"sqlite+aiosqlite:///{copy}"
        stamped = alembic(service_dir, prefix, "stamp", "head", url=copy_url)
        if stamped.returncode != 0:
            return False, "副本 stamp head 失败:\n" + tail(stamped)
        checked = alembic(service_dir, prefix, "check", url=copy_url)
        if checked.returncode != 0:
            return False, "库结构与迁移脚本不一致(未改动真实库):\n" + tail(checked)
        # 副本证明结构一致 → 真实库只写版本基线,不动任何业务表。
        adopted = alembic(service_dir, prefix, "stamp", "head")
        if adopted.returncode != 0:
            return False, "真实库 stamp head 失败:\n" + tail(adopted)
    return True, ""


def tail(proc: subprocess.CompletedProcess[str], lines: int = 12) -> str:
    """错误输出里过滤 alembic 的 INFO 噪声,只留可行动的信息。"""
    output = [
        line
        for line in (proc.stdout + proc.stderr).splitlines()
        if line.strip() and not line.startswith("INFO  [alembic")
    ]
    return "\n".join(output[-lines:])


def fail(name: str, message: str) -> bool:
    log(f"  ❌ {name}:{message}")
    return False


def migrate_service(name: str, prefix: str, default_db: str) -> bool:
    service_dir = ROOT / "services" / name
    if not (service_dir / "alembic.ini").exists():
        log(f"  ℹ️ {name}:无 alembic.ini,跳过")
        return True

    url = database_url(service_dir, prefix)
    if not url:
        url = f"sqlite+aiosqlite:///./{default_db}"
        log(f"  ℹ️ {name}:未配置 {prefix}__DATABASE_URL,按默认 {url} 处理")

    db = sqlite_file(url, service_dir)
    if db is None:
        if url.startswith("sqlite"):
            log(f"  ℹ️ {name}:内存 SQLite,无需迁移")
        else:
            scheme = url.split("://", 1)[0]
            log(
                f"  ℹ️ {name}:非 SQLite({scheme}),跳过自动迁移;"
                "请在服务目录手动执行 alembic upgrade head"
            )
        return True

    head = head_revision(service_dir, prefix)

    if not db.exists() or table_count(db) == 0:
        proc = upgrade_head(service_dir, prefix)
        if proc.returncode != 0:
            return fail(name, "空库建到 head 失败:\n" + tail(proc))
        log(f"  🆕 {name}:空库,已建到 {head}")
        return True

    revision = tracked_revision(db)
    if revision:
        if revision == head:
            log(f"  ✅ {name}:已是最新({head})")
            return True
        proc = upgrade_head(service_dir, prefix)
        if proc.returncode == 0:
            log(f"  ⬆️ {name}:{revision} → {head}")
            return True
        combined = f"{proc.stdout}\n{proc.stderr}".lower()
        if (
            "can't locate revision" not in combined
            and "no such revision" not in combined
        ):
            return fail(name, "迁移失败:\n" + tail(proc))
        log(f"  ℹ️ {name}:版本 {revision} 已不在迁移脚本里,尝试按 head 收养…")
    else:
        log(f"  ℹ️ {name}:库未登记 alembic 版本(create_all 建库),校验结构后收养…")

    ok, detail = adopt_untracked(service_dir, prefix, db, head)
    if not ok:
        return fail(name, detail)
    log(f"  🔖 {name}:结构与 {head} 一致,已登记版本基线")
    return True


def main() -> int:
    try:
        import alembic  # noqa: F401
    except ImportError:
        log(
            "❌ 当前 Python 缺少 alembic;请用共享 venv 运行:"
            "bash scripts/migrate-local.sh(或先 npx nx run agent-orchestrator:sync)"
        )
        return 1

    log("▶ 检查本地 SQLite 库是否落后 alembic head…")
    failed: list[str] = []
    for name, prefix, default_db in SERVICES:
        try:
            if not migrate_service(name, prefix, default_db):
                failed.append(name)
        except Exception as exc:  # noqa: BLE001 - 单个服务异常不拖垮其余服务
            log(f"  ❌ {name}:意外错误:{exc}")
            failed.append(name)

    if failed:
        log("")
        log(f"❌ 本地数据库迁移失败:{', '.join(failed)}")
        log("   库结构与迁移脚本不一致时,自动迁移不会改动已有表(避免破坏数据)。")
        log(
            "   若本地数据可丢:npm run dev:reset 后重新 npm run dev;"
            "否则请按上面的 alembic 输出手动处理。"
        )
        return 1
    log("✅ 本地数据库已对齐 alembic head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
