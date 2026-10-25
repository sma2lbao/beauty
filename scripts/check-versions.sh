#!/usr/bin/env bash
# check-versions.sh —— W4 R-34 版本一致性门禁。
#
# 断言:
#   1) 19 处版本字段全部 = 0.1.0(JS 7 + 根 pyproject + Python 11 成员);
#   2) CHANGELOG.md 存在且含 '## 0.1.0' 段;
#   3) nx.json 含 release 配置(conventionalCommits 与 tagPrefix)。
# 任一不满足退出非 0。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

FAILURES=0
EXPECTED='0.1.0'

# 1) 版本字段逐处断言
JS_FILES=(
  package.json
  apps/web/package.json
  apps/site/package.json
  apps/desktop/package.json
  packages/design/package.json
  packages/console-ui/package.json
  tools/eslint-config/package.json
)
for f in "${JS_FILES[@]}"; do
  actual=$(node -e "process.stdout.write(require('./$f').version || '')")
  if [ "$actual" != "$EXPECTED" ]; then
    echo "[FAIL] $f version=$actual (期望 $EXPECTED)"
    FAILURES=$((FAILURES + 1))
  fi
done

PY_FILES=(
  pyproject.toml
  packages/common/pyproject.toml
  packages/database/pyproject.toml
  packages/events/pyproject.toml
  services/agent-orchestrator/pyproject.toml
  services/llm-gateway/pyproject.toml
  services/connector-service/pyproject.toml
  services/knowledge-service/pyproject.toml
  services/account-service/pyproject.toml
  services/workspace-service/pyproject.toml
  services/tool-service/pyproject.toml
  services/bff/pyproject.toml
)
for f in "${PY_FILES[@]}"; do
  actual=$(grep -m1 '^version' "$f" | sed 's/version *= *"\([^"]*\)".*/\1/')
  if [ "$actual" != "$EXPECTED" ]; then
    echo "[FAIL] $f version=$actual (期望 $EXPECTED)"
    FAILURES=$((FAILURES + 1))
  fi
done

# 2) CHANGELOG 段存在
if ! grep -q "## $EXPECTED" CHANGELOG.md 2>/dev/null; then
  echo "[FAIL] CHANGELOG.md 缺 '## $EXPECTED' 段"
  FAILURES=$((FAILURES + 1))
fi

# 3) nx.json release 配置存在
if ! node -e "const n=require('./nx.json');const r=n.release||{};process.exit(r.conventionalCommits===true && r.git && r.git.tagPrefix==='v' && r.changelog && r.changelog.workspaceChangelog===true ? 0 : 1)"; then
  echo '[FAIL] nx.json release 配置不完整(需 conventionalCommits/tagPrefix v/workspaceChangelog)'
  FAILURES=$((FAILURES + 1))
fi

echo "== versions 汇总: ${FAILURES} 失败(19 版本字段 + CHANGELOG + release 配置) =="
[ "$FAILURES" -eq 0 ]
