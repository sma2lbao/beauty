#!/usr/bin/env bash
# check-docs.sh —— W1 R-16 文档门禁（RFC-002）
#
# 用法:
#   bash scripts/check-docs.sh          # 检查 docs 全部 Markdown
#
# 检查项:
#   1) prettier --check 格式;
#   2) Markdown 相对链接目标存在(文件/目录);
#   3) 锚点存在(链接 #anchor 在目标文件标题中可解析);
#   4) 归档文档不被「当前/唯一/真源」表述误引(防双真源,playbook §12.3);
#   5) 文档与代码事实漂移(端口/覆盖率/CI 规模/业务域/版本字段/已删除路径/工程路径存在性),
#      由 scripts/check-doc-drift.cjs 断言,详见该文件头注释。
#
# 纯 bash + node(仓库既有 prettier),不新增 npm 依赖。任一项失败退出非 0。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

FAILURES=0

# 1) prettier 格式检查
echo '== [docs] prettier 格式检查 =='
if ! npm_config_cache="$ROOT/.npm-cache" npx prettier --check 'docs/**/*.md'; then
  echo '[FAIL] prettier 格式'
  FAILURES=$((FAILURES + 1))
else
  echo '[PASS] prettier'
fi

# 2-4) 链接/锚点/归档引用检查(node 单文件实现,无新依赖)
echo '== [docs] 相对链接与锚点检查 =='
if node "$ROOT/scripts/check-docs-links.cjs"; then
  echo '[PASS] links'
else
  echo '[FAIL] links'
  FAILURES=$((FAILURES + 1))
fi

# 5) 文档与代码事实漂移(端口/覆盖率/CI/业务域/版本字段/已删除路径/工程路径存在性)
echo '== [docs] 文档漂移检查 =='
if node "$ROOT/scripts/check-doc-drift.cjs"; then
  echo '[PASS] drift'
else
  echo '[FAIL] drift'
  FAILURES=$((FAILURES + 1))
fi

echo "== docs 汇总: ${FAILURES} 失败 =="
[ "$FAILURES" -eq 0 ]
