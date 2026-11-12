#!/usr/bin/env bash
# check-workspace.sh —— W4 R-38 工作区卫生检查（RFC-005 §R-38）
#
# 三查:
#   1. 归档注记: docs/archive/**/*.md 每份必须含「已归档」注记（playbook §12.3）;
#   2. 文档索引: docs/README.md 必须索引 docs 下所有非归档 md（文件名或其父目录
#      的链接出现在索引中即算;docs/README.md 自身除外）;
#   3. 生成物外泄: git ls-files 不得出现 dist/ out/ coverage/ reports/ .nx/
#      test-output/ 路径（例外清单: apps/site/next-env.d.ts —— Next 官方要求提交,
#      .gitignore 注释已说明）。
#
# 用法: bash scripts/check-workspace.sh   # 无参数;任一失败 EXIT 1 并列出违规。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAILURES=0

# ── 检查 1: 归档注记 ──────────────────────────────────────────────
echo "== 检查 1/3: docs/archive 归档注记 =="
ARCHIVE_MISSING=0
while IFS= read -r f; do
  if ! grep -q "已归档" "$f"; then
    echo "  [违规] 归档文件缺「已归档」注记: $f"
    ARCHIVE_MISSING=$((ARCHIVE_MISSING + 1))
  fi
done < <(git ls-files "docs/archive/**/*.md" "docs/archive/*.md" 2>/dev/null | sort -u)
if [ "$ARCHIVE_MISSING" -gt 0 ]; then
  FAILURES=$((FAILURES + 1))
else
  echo "  [PASS] 归档注记齐全"
fi

# ── 检查 2: 文档索引覆盖 ──────────────────────────────────────────
echo "== 检查 2/3: docs/README.md 索引覆盖 =="
INDEX_MISSING=$(python3 -c '
import sys
from pathlib import Path

readme = Path("docs/README.md").read_text(encoding="utf-8")
tracked = sorted(str(p) for p in Path("docs").glob("**/*.md"))
missing = []
for s in tracked:
    if "docs/archive/" in s or s == "docs/README.md":
        continue
    p = Path(s)
    covered = p.name in readme or (str(p.parent) + "/") in readme
    if not covered:
        missing.append(s)
if missing:
    print(chr(10).join(missing))
' || true)
if [ -n "$INDEX_MISSING" ]; then
  echo "$INDEX_MISSING" | sed "s/^/  [违规] 未被索引: /"
  FAILURES=$((FAILURES + 1))
else
  echo "  [PASS] 索引覆盖完整"
fi

# ── 检查 3: tracked 生成物 ────────────────────────────────────────
echo "== 检查 3/3: tracked 生成物 =="
# 例外清单: Next.js 官方要求提交 next-env.d.ts（见 .gitignore 注释）
ALLOW_RE="^apps/site/next-env\.d\.ts$"
GEN_RE="(^|/)(dist|out|coverage|reports|\.nx|test-output)(/|$)"
LEAKS=$(git ls-files | grep -Ev "$ALLOW_RE" | grep -E "$GEN_RE" || true)
if [ -n "$LEAKS" ]; then
  echo "$LEAKS" | sed "s/^/  [违规] 生成物被跟踪: /"
  FAILURES=$((FAILURES + 1))
else
  echo "  [PASS] 无生成物外泄"
fi

echo "== 汇总: ${FAILURES} 项失败 =="
if [ "$FAILURES" -gt 0 ]; then
  exit 1
fi
exit 0
