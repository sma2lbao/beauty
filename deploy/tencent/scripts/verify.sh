#!/usr/bin/env bash
# 部署后自检：容器状态、网关健康、公网 TLS、资源余量。
set -uo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.cvm.yml --env-file .env)

host="$(grep -E '^API_HOST=' .env 2>/dev/null | cut -d= -f2- || true)"

echo "== 容器 =="
"${COMPOSE[@]}" ps

echo
echo "== 回环健康 =="
curl -fsS --max-time 3 http://127.0.0.1:8007/healthz && echo || echo "FAIL: 127.0.0.1:8007/healthz"

if [[ -n "${host}" ]]; then
  echo
  echo "== 公网 TLS =="
  curl -fsSI --max-time 8 "https://${host}/healthz" | head -5 ||
    echo "FAIL: https://${host}/healthz（检查 DNS、80/443、Caddy 日志）"
  echo
  echo "== 证书到期 =="
  echo | openssl s_client -servername "${host}" -connect "${host}:443" 2>/dev/null |
    openssl x509 -noout -dates 2>/dev/null || echo "WARN: 取不到证书"
fi

echo
echo "== 资源 =="
free -h
df -h / | tail -1
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' | head -12
