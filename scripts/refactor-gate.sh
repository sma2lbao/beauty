#!/usr/bin/env bash
# refactor-gate.sh —— W0 R-07 一键门禁脚本（docs/refactoring-playbook.md §6.2 接口）
#
# 用法:
#   bash scripts/refactor-gate.sh <nx 项目名> [--no-test] [--no-coverage]   # 单项目: lint → typecheck(若有) → test → build(若有)
#   bash scripts/refactor-gate.sh --affected                                # affected: npx nx affected -t lint,typecheck,test
#
# 约定（playbook §6.1/§6.3）:
#   - TS 项目 test 必须追加 -- --run（nx.json testMode: "watch"，否则 vitest 挂住）;不使用 test-ci（需 Nx Cloud）。
#   - TS 项目 test 默认追加 --coverage（与 ci.yml affected-js 逐条等价）;快速迭代可加 --no-coverage 关闭。
#   - @beauty/desktop 不能靠 nx test 透传参数（其 test target 连跑 renderer + node 两段,`--` 只会透传给最后一段）,
#     改为在 apps/desktop 下显式跑 `vitest run --config vitest.config.mts` 与 `vitest run --config vitest.node.config.mts`。
#   - 覆盖率阈值:TS 来自根 vitest.config.mts 的 thresholds;Python 来自各项目 pyproject addopts（自动生效,脚本不重复传参）。
#   - Python 项目固化 uv 缓存到仓库本地;外部已设 UV_CACHE_DIR/UV_PYTHON_INSTALL_DIR 则尊重不覆盖。
#   - 不访问网络、不安装依赖、不写仓库外目录（/tmp/refactor-gate-*.log 除外）;任一步失败退出非 0。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_LAST="/tmp/refactor-gate-last.log"
LOG_PROJECT="/tmp/refactor-gate-project.json"
# 全量步骤日志:追加写入,保留 test 步骤的 coverage 表供事后核对（不再每步覆盖）。
: >"$LOG_LAST"

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
  if { echo "== step: $label"; "$@"; } >>"$LOG_LAST" 2>&1; then
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
COVERAGE=1
AFFECTED=0

for arg in "$@"; do
  case "$arg" in
    --affected) AFFECTED=1 ;;
    --no-test) NO_TEST=1 ;;
    --no-coverage) COVERAGE=0 ;;
    --coverage) COVERAGE=1 ;;
    -h|--help) usage ;;
    -*) echo "未知参数: $arg" >&2; usage ;;
    *) if [ -n "$PROJECT" ]; then echo "只需一个项目名: $PROJECT / $arg" >&2; usage; fi
       PROJECT="$arg" ;;
  esac
done

if [ "$AFFECTED" -eq 1 ]; then
  echo "== refactor-gate: affected 模式 =="
  # 已知缺口:affected 的 test 集合同时含 Python 与 TS 项目,`nx affected -t test` 无法向两类项目分别透传
  # --run / --coverage（`--` 后的参数会整体传给每个被执行 target）,因此本模式不收集覆盖率;
  # 需要覆盖率门禁请逐项目跑（CI 的 affected 范围由 .github/workflows/ci.yml 的 affected-js job 负责）。
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
      # Python 覆盖率阈值在各项目 pyproject addopts 中自动生效（与 ci.yml python-test 等价）,不追加参数。
      run_step "test" npx nx test "$PROJECT"
    elif [ "$PROJECT" = "@beauty/desktop" ]; then
      # desktop 的 test target 连跑 renderer 与 node 两段,`--` 参数只会透传给最后一段（renderer 覆盖率会漏）;
      # 因此显式跑两段,各自带 --coverage（与 ci.yml affected-js 的 desktop 两步等价）。
      if [ "$COVERAGE" -eq 1 ]; then
        run_step "test:renderer" bash -c "cd apps/desktop && npx vitest run --config vitest.config.mts --coverage"
        run_step "test:node" bash -c "cd apps/desktop && npx vitest run --config vitest.node.config.mts --coverage"
      else
        run_step "test:renderer" bash -c "cd apps/desktop && npx vitest run --config vitest.config.mts"
        run_step "test:node" bash -c "cd apps/desktop && npx vitest run --config vitest.node.config.mts"
      fi
    else
      # vitest watch 模式会挂住,必须 -- --run（playbook §6.3）;
      # TS 项目默认再加 --coverage,与 ci.yml affected-js 逐条等价（--no-coverage 关闭）。
      if [ "$COVERAGE" -eq 1 ]; then
        run_step "test" npx nx test "$PROJECT" -- --run --coverage
      else
        run_step "test" npx nx test "$PROJECT" -- --run
      fi
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

# W1 R-16:文档门禁(prettier 格式 + 相对链接 + 锚点 + 归档误引)。
# 无论单项目还是 affected 模式都在最后执行:检查对象是 docs 全局,与项目无关;
# 失败计入 FAILURES 判 FAIL。
run_step "docs" bash scripts/check-docs.sh

echo "== 汇总: ${STEPS_TOTAL} 步执行, ${FAILURES} 失败 =="
if [ "$FAILURES" -gt 0 ]; then
  exit 1
fi
exit 0
