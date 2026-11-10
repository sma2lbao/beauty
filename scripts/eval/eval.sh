#!/usr/bin/env bash
# eval.sh —— P1-K/M1 评测 harness 包装脚本
#
# 用法:
#   bash scripts/eval/eval.sh                      # 跑全部内置套件并与基线比对
#   bash scripts/eval/eval.sh --suite task-approval
#   bash scripts/eval/eval.sh --update-baseline    # 人工更新 evals/baseline.json
#   bash scripts/eval/eval.sh --max-tokens 500 --max-total-tokens 2000
#
# 在 agent-orchestrator 的 uv 项目内运行(进程内 TestClient,脚本化模型,零网络)。
# 退出码 0 = 全通过且无 drift;1 = 有用例失败;2 = 仅与基线 drift。
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
exec uv run python -m agent_orchestrator.evals "$@"
