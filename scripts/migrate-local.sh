#!/usr/bin/env bash
# 把本地开发库迁移到 alembic head(scripts/dev.sh 启动前会自动调用)。
# 处理 SQLite 的 create_all 旧库、未登记版本、落后 revision;详见 migrate-local.py。
#
# 用法:
#   scripts/migrate-local.sh   # 或 npm run dev:migrate
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 缺 .env 时先由 .env.example 复制(与 dev.sh 的 ensure_env 一致),迁移才能读到库地址。
for svc in \
  account-service \
  agent-orchestrator \
  connector-service \
  knowledge-service \
  workspace-service \
  tool-service; do
  if [ ! -f "services/$svc/.env" ] && [ -f "services/$svc/.env.example" ]; then
    cp "services/$svc/.env.example" "services/$svc/.env"
    echo "  已创建 services/$svc/.env"
  fi
done

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "❌ 缺少共享 venv(.venv):先跑一次 npm run dev(会自动 uv sync)," >&2
  echo "   或手动执行 npx nx run agent-orchestrator:sync。" >&2
  exit 1
fi

# 本地库是 sqlite+aiosqlite,而 aiosqlite 只声明在各服务的 dev group:
# 根目录 uv sync 或单项目 uv run 都可能把它剪掉,alembic 异步引擎随后 import 失败。
if ! "$ROOT/.venv/bin/python" -c "import aiosqlite" >/dev/null 2>&1; then
  echo "⚠️  共享 venv 缺少 aiosqlite,SQLite 迁移会失败;先补齐全 workspace dev 依赖:" >&2
  echo "   npx nx run agent-orchestrator:sync --args=\"--all-packages --all-groups\"" >&2
fi

exec "$ROOT/.venv/bin/python" "$ROOT/scripts/migrate-local.py"
