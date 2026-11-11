#!/usr/bin/env bash
# 一次性迁移作业：6 个库按依赖顺序 alembic upgrade head（幂等，可重复执行）。
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.cvm.yml --env-file .env)

if [[ ! -f .env ]]; then
  echo "缺少 .env：先 cp .env.example .env 并逐项填写" >&2
  exit 1
fi

"${COMPOSE[@]}" run --rm migrate
