#!/usr/bin/env bash
# check-python-size.sh —— W5 R-45 Python 体量 ratchet（RFC-006 §4 R-45）
#
# 判据: services/ 与 packages/ 下所有 .py（排除 migrations/ 与构建产物目录）单文件 ≤500 行;
#       源文件与测试文件同一口径（W4 R-39a/R-39b 的出口指标）。
# 例外: 内联白名单（路径 + 原因）。命中白名单只打 WARN,不计失败;未登记的违规 EXIT 1。
# 用法: bash scripts/check-python-size.sh
#
# 说明: 用 git ls-files --cached --others --exclude-standard 取文件,
#       这样未提交的新增文件也会被检查（防止拆分过程中漏扫）;
#       行数用 awk 'END{print NR}' 统计,避免无尾换行文件被 wc -l 少算一行。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LIMIT=500

# ── 例外登记（RFC-006 §5 D1）────────────────────────────────────
# 每条: <仓库相对路径>|<原因>
ALLOWLIST=(
  "services/tool-service/helpers/fs_helper.py|D1: python3 -c 注入沙箱的单文件 stdlib 脚本,拆分需改执行契约"
)

is_allowed() {
  local target="$1"
  local entry
  for entry in "${ALLOWLIST[@]}"; do
    if [ "${entry%%|*}" = "$target" ]; then
      ALLOW_REASON="${entry#*|}"
      return 0
    fi
  done
  return 1
}

echo "== Python 体量检查: 单文件 ≤${LIMIT} 行（排除 migrations/）=="

VIOLATIONS=()
WARNINGS=()
SCANNED=0

while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in
    *.py) ;;
    *) continue ;;
  esac
  # 排除迁移与生成物（git 已忽略的通常不会出现,这里兜底显式目录）
  case "$f" in
    */migrations/* | */.venv/* | */node_modules/* | */dist/* | */out-tsc/* | */.nx/* | */coverage/* | */reports/*) continue ;;
  esac
  lines=$(awk 'END { print NR }' "$f")
  SCANNED=$((SCANNED + 1))
  if [ "$lines" -gt "$LIMIT" ]; then
    if is_allowed "$f"; then
      WARNINGS+=("$lines $f ($ALLOW_REASON)")
    else
      VIOLATIONS+=("$lines $f")
    fi
  fi
done < <(git ls-files --cached --others --exclude-standard -- 'services/**/*.py' 'packages/**/*.py' | sort -u)

echo "  扫描 $SCANNED 个 .py 文件（阈值 ${LIMIT}）"

if [ "${#WARNINGS[@]}" -gt 0 ]; then
  echo "  [WARN] 白名单内超限文件（不阻断）:"
  printf '%s\n' "${WARNINGS[@]}" | sort -rn | sed 's/^/    /'
fi

if [ "${#VIOLATIONS[@]}" -gt 0 ]; then
  echo "  [违规] 超过 ${LIMIT} 行:"
  printf '%s\n' "${VIOLATIONS[@]}" | sort -rn | sed 's/^/    /'
  echo "  [FAIL] ${#VIOLATIONS[@]} 个文件超限（拆分或按 RFC 登记例外）"
  exit 1
fi

echo "  [PASS] 无未登记的超限文件"
exit 0
