#!/usr/bin/env bash
# platform.sh —— 统一评测平台(evalkit)包装脚本
#
# 用法:
#   bash scripts/eval/platform.sh                     # 跑全部配置套件并与基线比对
#   bash scripts/eval/platform.sh list --cases
#   bash scripts/eval/platform.sh run --suite agent-core
#   bash scripts/eval/platform.sh run --update-baseline
#   bash scripts/eval/platform.sh validate
#
# 零网络:Agent 适配器走进程内 TestClient + 脚本化模型,检索适配器只读固定语料。
# 退出码 0 = 全通过且无 drift;1 = 用例失败/阈值不达标;2 = 仅与基线 drift;
# 3 = 配置、数据集或适配器错误。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 受限环境(如 agent 沙箱)下 uv 默认缓存不可写:优先用仓库本地缓存
if [ -z "${UV_CACHE_DIR:-}" ] && [ -d "$ROOT/.uv-cache" ]; then
  export UV_CACHE_DIR="$ROOT/.uv-cache"
fi
if [ -z "${UV_PYTHON_INSTALL_DIR:-}" ] && [ -d "$ROOT/.uv-python" ]; then
  export UV_PYTHON_INSTALL_DIR="$ROOT/.uv-python"
fi

cd "$ROOT"
exec uv run python -m evalkit "$@"
