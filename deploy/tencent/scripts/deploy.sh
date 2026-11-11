#!/usr/bin/env bash
# 拉取镜像并启动全部服务；等待 api-gateway 健康后打印状态。
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.cvm.yml --env-file .env)

if [[ ! -f .env ]]; then
  echo "缺少 .env：先 cp .env.example .env 并逐项填写" >&2
  exit 1
fi
if grep -qE '^(TAG=change-me|REGISTRY=.*change-me)' .env; then
  echo ".env 里 TAG/REGISTRY 仍是占位值，先填真实镜像" >&2
  exit 1
fi

"${COMPOSE[@]}" pull
"${COMPOSE[@]}" up -d --remove-orphans

echo "等待 api-gateway 健康（最多 120s）…"
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:8007/healthz >/dev/null 2>&1; then
    "${COMPOSE[@]}" ps
    host="$(grep -E '^API_HOST=' .env | cut -d= -f2-)"
    echo "OK：https://${host} 就绪（证书由 Caddy 自动申请）"
    exit 0
  fi
  sleep 2
done

echo "api-gateway 未在 120s 内健康，最近日志：" >&2
"${COMPOSE[@]}" ps >&2
"${COMPOSE[@]}" logs --tail=80 api-gateway caddy >&2
exit 1
