#!/usr/bin/env bash
# refactor-gate.sh —— W0 R-07 一键门禁脚本（docs/refactoring-playbook.md §6.2 接口）
#
# 用法:
#   bash scripts/refactor-gate.sh <nx 项目名> [--no-test]   # 单项目: lint → typecheck(若有) → test → build(若有)
#   bash scripts/refactor-gate.sh --affected                # affected: npx nx affected -t lint,typecheck,test
#
# 约定（playbook §6.3）:
#   - TS 项目 test 必须追加 -- --run（nx.json testMode: "watch"，否则 vitest 挂住）;不使用 test-ci（需 Nx Cloud）。
#   - Python 项目固化 uv 缓存到仓库本地;外部已设 UV_CACHE_DIR/UV_PYTHON_INSTALL_DIR 则尊重不覆盖。
#   - 不访问网络、不安装依赖、不写仓库外目录（/tmp/refactor-gate-*.log 除外）;任一步失败退出非 0。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_LAST="/tmp/refactor-gate-last.log"
LOG_PROJECT="/tmp/refactor-gate-project.json"

FAILURES=0
STEPS_TOTAL=0

# 固化 uv 缓存到仓库本地（外部已设则尊重;与 project.json target 内 R-01 同语义）
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$ROOT/.uv-python}"

# run_step <标签> <命令...>: 打印 PASS/FAIL 与耗时;失败不中断,最后汇总退出码。
run_step() {
  local label="$1"
  shift
  local start elapsed
  start=$(date +%s)
  STEPS_TOTAL=$((STEPS_TOTAL + 1))
  if "$@" >"$LOG_LAST" 2>&1; then
    elapsed=$(($(date +%s) - start))
    printf "[PASS] %-10s (%ss)\n" "$label" "$elapsed"
  else
    elapsed=$(($(date +%s) - start))
    FAILURES=$((FAILURES + 1))
    printf "[FAIL] %-10s (%ss)\n" "$label" "$elapsed"
    tail -n 20 "$LOG_LAST" | sed "s/^/    | /"
  fi
}

usage() {
  grep "^# " "${BASH_SOURCE[0]}" | sed "s/^# \{0,1\}//" | head -n 12
  exit 2
}

PROJECT=""
NO_TEST=0
AFFECTED=0

for arg in "$@"; do
  case "$arg" in
    --affected) AFFECTED=1 ;;
    --no-test) NO_TEST=1 ;;
    -h|--help) usage ;;
    -*) echo "未知参数: $arg" >&2; usage ;;
    *) if [ -n "$PROJECT" ]; then echo "只需一个项目名: $PROJECT / $arg" >&2; usage; fi
       PROJECT="$arg" ;;
  esac
done

if [ "$AFFECTED" -eq 1 ]; then
  echo "== refactor-gate: affected 模式 =="
  run_step "affected" npx nx affected -t lint,typecheck,test --base=origin/main
else
  if [ -z "$PROJECT" ]; then
    echo "错误: 缺少项目名（或用 --affected）" >&2
    usage
  fi

  if ! npx nx show project "$PROJECT" --json >"$LOG_PROJECT" 2>/dev/null; then
    echo "错误: Nx 项目不存在或无法解析: $PROJECT" >&2
    exit 2
  fi

  # 一次解析: Python 与否 + typecheck/test/build 是否存在
  read -r IS_PYTHON HAS_TYPECHECK HAS_TEST HAS_BUILD < <(python3 - "$LOG_PROJECT" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
targets = d.get("targets", {})
lint_exec = (targets.get("lint", {}) or {}).get("executor", "")
is_python = "1" if lint_exec.startswith("@nxlv/python") else "0"
has_typecheck = "1" if "typecheck" in targets else "0"
has_test = "1" if "test" in targets else "0"
has_build = "1" if "build" in targets else "0"
print(f"{is_python} {has_typecheck} {has_test} {has_build}")
PYEOF
)

  LANG_LABEL="TS/npm"
  if [ "$IS_PYTHON" = "1" ]; then LANG_LABEL="Python/uv"; fi
  echo "== refactor-gate: ${PROJECT} (${LANG_LABEL}) =="

  run_step "lint" npx nx lint "$PROJECT"

  if [ "$HAS_TYPECHECK" = "1" ]; then
    run_step "typecheck" npx nx typecheck "$PROJECT"
  else
    echo "[SKIP] typecheck  (无该 target)"
  fi

  if [ "$NO_TEST" -eq 1 ]; then
    echo "[SKIP] test       (--no-test)"
  elif [ "$HAS_TEST" = "1" ]; then
    if [ "$IS_PYTHON" = "1" ]; then
      run_step "test" npx nx test "$PROJECT"
    else
      # vitest watch 模式会挂住,必须 -- --run（playbook §6.3）
      run_step "test" npx nx test "$PROJECT" -- --run
    fi
  else
    echo "[SKIP] test       (无该 target)"
  fi

  if [ "$HAS_BUILD" = "1" ]; then
    run_step "build" npx nx build "$PROJECT"
  else
    echo "[SKIP] build      (无该 target)"
  fi
fi

echo "== 汇总: ${STEPS_TOTAL} 步执行, ${FAILURES} 失败 =="
if [ "$FAILURES" -gt 0 ]; then
  exit 1
fi
exit 0
