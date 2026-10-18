#!/usr/bin/env bash
# 清空本地开发数据(SQLite + 本地产物),保留 .env 与源码。
# 用法:
#   scripts/reset-local.sh          # 交互确认
#   scripts/reset-local.sh --yes    # 不确认
#   scripts/reset-local.sh --force  # 服务仍在运行也强制删除(不推荐)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

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

# 服务运行时删 SQLite 可能导致状态错乱
running=0
for port in 8001 8002 8003 8004 8005 8006 8007; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
    running=1
  fi
done
if [ "$running" = "1" ] && [ "$FORCE" = "0" ]; then
  echo "检测到服务仍在运行(端口 8001–8007)。" >&2
  echo "请先 Ctrl+C 停止 scripts/dev.sh,或加 --force 强制删除。" >&2
  exit 1
fi

echo "将删除以下本地数据:"
find services -maxdepth 2 -name '*.db' -print | sed 's/^/  /'
find services -maxdepth 2 -type d -name '.artifacts' -print | sed 's/^/  /'
echo "  .cache/dev"

if [ "$YES" = "0" ]; then
  read -r -p "确认继续?[y/N] " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) echo "已取消"; exit 0 ;;
  esac
fi

find services -maxdepth 2 -name '*.db' -delete
find services -maxdepth 2 -type d -name '.artifacts' -prune -exec rm -rf {} +
rm -rf "$ROOT/.cache/dev"

echo "已重置。重新执行 scripts/dev.sh 启动;AUTO_CREATE_TABLES=true 会自动建表。"
