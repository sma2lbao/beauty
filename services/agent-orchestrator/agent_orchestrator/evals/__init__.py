"""评测 harness(P1-K M1,契约 docs/design/eval-and-cost-dashboard.md §2)。

在进程内用脚本化模型 + 合成工具跑三类场景(直答/任务审批/策略生效),
断言 run 事件、工具调用、交付物与 token 用量,并与提交在仓库里的基线
比对;与 scripts/golden-run 同一套零网络口径,但 golden-run 是单条冒烟,
这里是可回归、可基线的用例集。

入口:bash scripts/eval/eval.sh(等价 uv run python -m agent_orchestrator.evals)。
"""

from __future__ import annotations

from .report import build_report, compare, print_table
from .runner import CaseResult, run_case, run_suites
from .suites import Case, Suite, build_suites

__all__ = [
    "Case",
    "CaseResult",
    "Suite",
    "build_report",
    "build_suites",
    "compare",
    "print_table",
    "run_case",
    "run_suites",
]
