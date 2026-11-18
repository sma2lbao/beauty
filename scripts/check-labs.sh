#!/usr/bin/env bash
# check-labs.sh —— P2「案例实验室」围栏校验。
#
# 断言（对应 docs/design/blog.md §6.3/§6.4）:
#   1. labs/ 整体被 git 忽略（内容不会进 clone/CI/安全扫描）;
#   2. labs/ 下没有已跟踪文件（防止 git add -f 绕过）;
#   3. Nx 项目图不含 labs/ 下的项目（.nxignore 生效）;
#   4. 根 package-lock.json / uv.lock 不含 labs 引用;
#   5. 根 package.json workspaces 与根 pyproject.toml uv members 未被改写。
#
# 用法: bash scripts/check-labs.sh   # 无参数;任一失败 EXIT 1 并列出违规。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAILURES=0

fail() {
  echo "  [违规] $*"
  FAILURES=$((FAILURES + 1))
}

# ── 检查 1: labs 被忽略 ──────────────────────────────────────────
echo "== 检查 1/5: labs/ 是否被 git 忽略 =="
if git check-ignore -q -- "labs/" || git check-ignore -q -- "labs/.probe"; then
  echo "  [PASS] labs/ 已被 .gitignore 忽略"
else
  fail "labs/ 未被忽略;在根 .gitignore 增加 /labs/"
fi

# ── 检查 2: labs 无跟踪文件 ──────────────────────────────────────
echo "== 检查 2/5: labs/ 是否有被跟踪文件 =="
TRACKED=$(git ls-files -- labs)
if [ -n "$TRACKED" ]; then
  echo "$TRACKED" | sed 's/^/  [违规] 已被跟踪: /'
  FAILURES=$((FAILURES + 1))
else
  echo "  [PASS] labs/ 无跟踪文件"
fi

# ── 检查 3: Nx 项目图不含 labs ───────────────────────────────────
echo "== 检查 3/5: Nx 项目图是否包含 labs =="
if [ ! -d labs ]; then
  echo "  [PASS] labs/ 不存在,无需推断项目"
else
  GRAPH="${TMPDIR:-/tmp}/nx-graph-labs-$$.json"
  trap 'rm -f "$GRAPH"' EXIT
  if npx nx graph --file="$GRAPH" >/dev/null 2>&1 &&
    node -e '
      const graph = require(process.argv[1]).graph || { nodes: {} };
      const bad = Object.values(graph.nodes || {}).filter((node) => {
        const root = (node.data && node.data.root) || "";
        return root === "labs" || root.startsWith("labs/");
      });
      if (bad.length) {
        console.error(bad.map((n) => n.name + " @ " + n.data.root).join("\n"));
        process.exit(1);
      }
    ' "$GRAPH"; then
    echo "  [PASS] Nx 项目图不含 labs"
  else
    fail "Nx 项目图包含 labs 项目,或 Nx 图生成失败;检查 .nxignore 与 labs/ 下的 project.json/tsconfig.json"
  fi
fi

# ── 检查 4: 根锁文件不含 labs ────────────────────────────────────
echo "== 检查 4/5: 根锁文件是否引用 labs =="
if grep -Eq '(^|["/])labs/' package-lock.json uv.lock 2>/dev/null; then
  fail "package-lock.json / uv.lock 含 labs 引用;案例依赖必须用自己的锁文件"
else
  echo "  [PASS] 根锁文件无 labs 引用"
fi

# ── 检查 5: workspaces / uv members 未被改写 ─────────────────────
echo "== 检查 5/5: 根 workspaces / uv members =="
if node -e '
  const pkg = require("./package.json");
  const bad = (pkg.workspaces || []).filter((w) => /(^|\/)labs(\/|$)/.test(w));
  if (bad.length) {
    console.error("  [违规] package.json workspaces 含 labs: " + bad.join(", "));
    process.exit(1);
  }
'; then
  echo "  [PASS] package.json workspaces 不含 labs"
else
  FAILURES=$((FAILURES + 1))
fi

if python3 - <<'PY'
import tomllib
from pathlib import Path

data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
members = data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
bad = [m for m in members if m == "labs" or m.startswith("labs/")]
if bad:
    print("  [违规] pyproject.toml uv members 含 labs: " + ", ".join(bad))
    raise SystemExit(1)
PY
then
  echo "  [PASS] pyproject.toml uv members 不含 labs"
else
  FAILURES=$((FAILURES + 1))
fi

echo "== labs 围栏汇总: ${FAILURES} 项失败 =="
if [ "$FAILURES" -gt 0 ]; then
  exit 1
fi
exit 0
