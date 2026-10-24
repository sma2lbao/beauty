#!/usr/bin/env bash
# 本地开发一键启动(无需 docker/云依赖):
#   - 六个业务服务 + FastAPI BFF + Vite SPA
#   - 缺 .env 时自动从 .env.example 复制
#   - 统一日志与 Ctrl+C 清理
#
# 用法:
#   scripts/dev.sh              # 启动后端 + BFF + web
#   scripts/dev.sh --site       # 额外启动 Next.js 官网(:3000)
#   scripts/dev.sh --no-web     # 只启动 Python 服务
#   scripts/dev.sh --status     # 只检查健康状态
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/.cache/dev"
mkdir -p "$LOG_DIR"

SERVICES=(
  "account-service|8001|/healthz"
  "agent-orchestrator|8002|/healthz"
  "llm-gateway|8003|/healthz"
  "connector-service|8004|/healthz"
  "workspace-service|8005|/healthz"
  "knowledge-service|8006|/healthz"
  "bff|8007|/api/health"
)

MODE="start"
WITH_WEB=1
WITH_SITE=0
PIDS=()

for arg in "$@"; do
  case "$arg" in
    --status) MODE="status" ;;
    --no-web) WITH_WEB=0 ;;
    --site) WITH_SITE=1 ;;
    -h|--help)
      sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "未知参数: $arg" >&2
      exit 2
      ;;
  esac
done

# 受限环境(如 agent sandbox)下把 uv 缓存放仓库内;普通环境不覆盖已有配置
if [ -d "$ROOT/.uv-cache" ] && [ -z "${UV_CACHE_DIR:-}" ]; then
  export UV_CACHE_DIR="$ROOT/.uv-cache"
fi
if [ -d "$ROOT/.uv-python" ] && [ -z "${UV_PYTHON_INSTALL_DIR:-}" ]; then
  export UV_PYTHON_INSTALL_DIR="$ROOT/.uv-python"
fi

# 端口探测:lsof 缺失或不可用时回退 curl(探测者只判断"是否被占",不区分占用者);
# 输出 pid 供日志,无 lsof 时输出空串。macOS/Linux 通用。
port_pid() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1 || true
  else
    # 回退:TCP 可连即视为已占用(拿不到 pid,但能避免重复启动)
    if curl -fsS --max-time 1 "http://127.0.0.1:$port/" >/dev/null 2>&1 \
      || nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      echo "(lsof 不可用,端口被占)"
    fi
  fi
}

healthy() {
  curl -fsS --max-time 2 "http://127.0.0.1:$1$2" >/dev/null 2>&1
}

web_healthy() {
  curl -fsS --max-time 2 "http://localhost:5173/" >/dev/null 2>&1
}

site_healthy() {
  curl -fsS --max-time 2 "http://localhost:3000/" >/dev/null 2>&1
}

ensure_env() {
  local svc="$1"
  if [ ! -f "$ROOT/services/$svc/.env" ] && [ -f "$ROOT/services/$svc/.env.example" ]; then
    cp "$ROOT/services/$svc/.env.example" "$ROOT/services/$svc/.env"
    echo "  已创建 services/$svc/.env"
  fi
}

wait_health() {
  local name="$1" port="$2" path="$3"
  local i
  for i in $(seq 1 120); do
    if healthy "$port" "$path"; then
      return 0
    fi
    sleep 1
  done
  echo "❌ $name 启动超时(:$port);日志:" >&2
  tail -n 30 "$LOG_DIR/$name.log" >&2 || true
  return 1
}

if [ "$MODE" = "status" ]; then
  failed=0
  for spec in "${SERVICES[@]}"; do
    IFS="|" read -r name port path <<< "$spec"
    if healthy "$port" "$path"; then
      echo "✅ $name  http://127.0.0.1:$port"
    else
      echo "❌ $name  未就绪(:$port)"
      failed=1
    fi
  done
  if web_healthy; then echo "✅ web    http://localhost:5173"; else echo "❌ web    未就绪(:5173)"; failed=1; fi
  if site_healthy; then echo "✅ site   http://localhost:3000"; else echo "ℹ️  site   未启动(可选)"; fi
  exit "$failed"
fi

cleanup() {
  trap - INT TERM EXIT
  echo ""
  echo "停止本地开发进程..."
  if [ ${#PIDS[@]} -gt 0 ]; then
    for pid in "${PIDS[@]}"; do
      # 先温和终止;进程可能已退出(正常或超时被 kill)。
      if kill -0 "$pid" 2>/dev/null; then
        # 杀整个子进程组(负 pid):nx/npx 会派生 uvicorn/vite 子进程,
        # 只杀父进程会留孤儿继续占端口(已知问题:kill nx 父进程不清理子进程)。
        # 后台任务以本 shell 为组长,kill -- -pid 对脚本自启进程有效;
        # 若进程组不可用(非组长)则退化为只杀 pid,再 pkill 兜底。
        kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
      fi
    done
    # 兜底:按命令行特征清掉本脚本拉起的 uvicorn/vite 孤儿(仅限我们的端口)。
    sleep 1
    for port in 8001 8002 8003 8004 8005 8006 8007 5173 3000; do
      local orphan
      orphan="$(port_pid "$port")"
      # 空 = 未占用;含「lsof 不可用」= 探测回退模式拿不到 pid,跳过。
      if [ -z "$orphan" ] || [ "${orphan#*lsof}" != "$orphan" ]; then
        continue
      fi
      # 端口仍被占:若是 uvicorn/vite/node(nx run 启动的服务进程),补杀。
      if command -v lsof >/dev/null 2>&1; then
        if lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | grep -qE 'uvicorn|vite|node'; then
          kill "$orphan" 2>/dev/null || true
          echo "  已清理 :$port 残留进程(pid $orphan)"
        fi
      fi
    done
  fi
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# 首次启动前同步共享 venv(根 .venv 缺失或 uv.lock 较新时);
# 失败不阻断(已同步过的环境 sync 很快,失败会在服务启动时再暴露)。
if [ ! -d "$ROOT/.venv" ] || [ "$ROOT/uv.lock" -nt "$ROOT/.venv" ]; then
  echo "▶ 同步共享 venv(首次启动或 uv.lock 有更新)…"
  if ! (cd "$ROOT" && npx nx run agent-orchestrator:sync); then
    echo "⚠️  uv sync 失败,继续尝试启动(若服务报缺依赖,重跑前先手动 npx nx run <服务>:sync)" >&2
  fi
fi

# 本脚本记录已启动的端口(区分"我们启动的"与"外部占用的")。
# 用空格分隔字符串而非关联数组:macOS 自带 bash 3.2 无 declare -A,
# 在 set -e 下会直接中止脚本。
STARTED_PORTS=" "

for spec in "${SERVICES[@]}"; do
  IFS="|" read -r name port path <<< "$spec"
  ensure_env "$name"
  existing="$(port_pid "$port")"
  if [ -n "$existing" ]; then
    case "$STARTED_PORTS" in
      *" $port "*) is_started=1 ;;
      *) is_started=0 ;;
    esac
    if [ "$is_started" = "1" ]; then
      echo "⚠️  $name 的 :$port 是本脚本先前启动的实例,跳过"
    else
      echo "⚠️  $name 端口 :$port 已被外部进程(pid $existing)占用,跳过(不属于本脚本,不会清理)"
    fi
    continue
  fi
  echo "▶ 启动 $name(:$port)"
  (cd "$ROOT" && npx nx run "$name:serve") > "$LOG_DIR/$name.log" 2>&1 &
  PIDS+=("$!")
  STARTED_PORTS="$STARTED_PORTS$port "
done

if [ "$WITH_WEB" = "1" ]; then
  if [ -n "$(port_pid 5173)" ]; then
    echo "⚠️  web 已在 :5173 运行,跳过"
  else
    echo "▶ 启动 web(:5173)"
    (cd "$ROOT" && npx nx dev @beauty/web) > "$LOG_DIR/web.log" 2>&1 &
    PIDS+=("$!")
  fi
fi

if [ "$WITH_SITE" = "1" ]; then
  if [ -n "$(port_pid 3000)" ]; then
    echo "⚠️  site 已在 :3000 运行,跳过"
  else
    echo "▶ 启动 site(:3000)"
    (cd "$ROOT" && npx nx run @beauty/site:dev) > "$LOG_DIR/site.log" 2>&1 &
    PIDS+=("$!")
  fi
fi

echo "等待服务就绪(首次启动可能要先 uv sync)…"
failed=0
for spec in "${SERVICES[@]}"; do
  IFS="|" read -r name port path <<< "$spec"
  if ! wait_health "$name" "$port" "$path"; then
    failed=1
  else
    echo "✅ $name :$port"
  fi
done

if [ "$WITH_WEB" = "1" ] && [ -f "$LOG_DIR/web.log" ]; then
  if ! web_healthy; then
    echo "❌ web 启动超时(:5173);日志:" >&2
    tail -n 30 "$LOG_DIR/web.log" >&2 || true
    failed=1
  else
    echo "✅ web http://localhost:5173"
  fi
fi

if [ "$WITH_SITE" = "1" ] && [ -f "$LOG_DIR/site.log" ]; then
  if ! site_healthy; then
    echo "❌ site 启动超时(:3000);日志:" >&2
    tail -n 30 "$LOG_DIR/site.log" >&2 || true
    failed=1
  else
    echo "✅ site http://localhost:3000"
  fi
fi

if [ "$failed" -ne 0 ]; then
  echo "有服务未就绪,请查看 $LOG_DIR/*.log" >&2
  exit 1
fi

echo ""
echo "本地开发已就绪:"
if [ "$WITH_WEB" = "1" ]; then echo "  Web      http://localhost:5173"; fi
if [ "$WITH_SITE" = "1" ]; then echo "  官网     http://localhost:3000"; fi
echo "  BFF      http://127.0.0.1:8007/api/health"
echo "  日志     $LOG_DIR"
echo "  Ctrl+C 停止本脚本启动的进程"
echo ""

# 任一子进程退出即整体退出
while true; do
  if [ ${#PIDS[@]} -gt 0 ]; then
    for pid in "${PIDS[@]}"; do
      if ! kill -0 "$pid" 2>/dev/null; then
        echo "⚠️  子进程 $pid 已退出,停止其余进程" >&2
        exit 1
      fi
    done
  fi
  sleep 2
done