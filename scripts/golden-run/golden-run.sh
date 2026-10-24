#!/usr/bin/env bash
# golden-run.sh —— R-33 黄金 run 包装脚本
#
# 用法:
#   bash scripts/golden-run/golden-run.sh           # 临时 sqlite,跑完清理
#   bash scripts/golden-run/golden-run.sh --keep    # 保留 sqlite 到 .cache/golden-run/
#
# 在 agent-orchestrator 的 uv 项目内运行(进程内 TestClient,零网络、零外部服务)。
# 退出码 0 = 全部阶段 PASS;1 = 失败;环境问题(uv/依赖未就绪)由 uv 直接报错。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVICE_DIR="$ROOT/services/agent-orchestrator"

# 受限环境(如 agent 沙箱)下 uv 默认缓存不可写:优先用仓库本地缓存
if [ -z "${UV_CACHE_DIR:-}" ] && [ -d "$ROOT/.uv-cache" ]; then
  export UV_CACHE_DIR="$ROOT/.uv-cache"
fi
if [ -z "${UV_PYTHON_INSTALL_DIR:-}" ] && [ -d "$ROOT/.uv-python" ]; then
  export UV_PYTHON_INSTALL_DIR="$ROOT/.uv-python"
fi

cd "$SERVICE_DIR"
exec uv run python "$SCRIPT_DIR/golden_run.py" "$@"
