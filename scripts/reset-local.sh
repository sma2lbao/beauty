#!/usr/bin/env bash
# 清空本地开发数据(Postgres 各服务库 + SQLite 覆盖库 + 本地产物),保留 .env 与源码。
# 用法:
#   scripts/reset-local.sh          # 交互确认
#   scripts/reset-local.sh --yes    # 不确认
#   scripts/reset-local.sh --force  # 服务仍在运行也强制删除(不推荐)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 与服务默认配置一致(见 deploy/postgres/initdb/01-create-databases.sql)。
PG_DATABASES=(
  beauty_account
  beauty_agent
  beauty_connector
  beauty_knowledge
  beauty_workspace
  beauty_tool
)

YES=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --force) FORCE=1 ;;
    -h|--help)
      sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

# 服务运行时删库可能导致状态错乱;lsof 不可用时用 TCP 探测回退。
running=0
for port in 8001 8002 8003 8004 8005 8006 8007 8008; do
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1 && running=1
  else
    (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null && { running=1; exec 3>&- 3<&-; }
  fi
done
if [ "$running" = "1" ] && [ "$FORCE" = "0" ]; then
  echo "检测到服务仍在运行(端口 8001–8008)。" >&2
  echo "请先 Ctrl+C 停止 scripts/dev.sh,或加 --force 强制删除。" >&2
  exit 1
fi

echo "将重置以下本地数据:"
echo "  Postgres 库(本机 compose,drop 后重建):${PG_DATABASES[*]}"
find services -maxdepth 2 -name '*.db' -print | sed 's/^/  /'
find services -maxdepth 2 -type d -name '.artifacts' -print | sed 's/^/  /'
find services -maxdepth 2 -type d -name '.cache' -print | sed 's/^/  /'
echo "  .cache/dev"

if [ "$YES" = "0" ]; then
  read -r -p "确认继续?[y/N] " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) echo "已取消"; exit 0 ;;
  esac
fi

# 重置 Postgres:只 drop/recreate 每服务独立库,不动 compose 实例本身;
# 连不上(未起 compose)或缺 asyncpg 时跳过,不阻塞后面的文件清理。
reset_postgres() {
  local python="$ROOT/.venv/bin/python"
  if [ ! -x "$python" ]; then
    echo "ℹ️  缺少共享 venv(.venv),跳过 Postgres 重置(先跑一次 npm run dev)。"
    return 0
  fi
  "$python" - <<'PY'
import asyncio
import sys

try:
    import asyncpg
except ImportError:
    print("ℹ️  共享 venv 缺少 asyncpg,跳过 Postgres 重置", file=sys.stderr)
    raise SystemExit(0)

DATABASES = (
    "beauty_account",
    "beauty_agent",
    "beauty_connector",
    "beauty_knowledge",
    "beauty_workspace",
    "beauty_tool",
)
ADMIN_URL = "postgresql://beauty:beauty@localhost:5432/beauty"


async def reset() -> int:
    try:
        conn = await asyncpg.connect(ADMIN_URL, timeout=3)
    except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
        print(f"ℹ️  本地 Postgres 未就绪({exc!r}),跳过 Postgres 重置", file=sys.stderr)
        return 0
    try:
        for name in DATABASES:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{name}" OWNER beauty')
            print(f"  已重置 Postgres 库 {name}")
    finally:
        await conn.close()
    return 0


raise SystemExit(asyncio.run(reset()))
PY
}

reset_postgres

find services -maxdepth 2 -name '*.db' -delete
find services -maxdepth 2 -type d -name '.artifacts' -prune -exec rm -rf {} +
# W1 R-15:同时清 services/*/.cache(account-service 签名密钥持久化在此,
# 旧实现漏删会导致重置后旧密钥仍被使用)。
find services -maxdepth 2 -type d -name '.cache' -prune -exec rm -rf {} +
rm -rf "$ROOT/.cache/dev"

echo "已重置。重新执行 scripts/dev.sh 启动;启动前会自动迁移到 alembic head。"
