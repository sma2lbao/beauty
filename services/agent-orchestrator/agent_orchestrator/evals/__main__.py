"""评测 CLI:python -m agent_orchestrator.evals(设计 §2.4)。

默认跑全部内置套件并与提交在仓库里的基线比对;退出码是 CI 契约:
0 = 全通过且无 drift,1 = 有用例失败,2 = 仅基线 drift。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .report import (
    DEFAULT_BASELINE,
    baseline_payload,
    build_report,
    compare,
    default_report_path,
    load_baseline,
    print_table,
    write_report,
)
from .runner import CaseResult, run_suites
from .suites import Suite, build_suites, suites_from_payload

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DRIFT = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent_orchestrator.evals",
        description="beauty-moon 智能体评测 harness(脚本化模型、进程内、零网络)",
    )
    parser.add_argument(
        "--suite",
        action="append",
        default=None,
        metavar="NAME",
        help="只跑指定套件(可重复);缺省跑全部内置套件",
    )
    parser.add_argument(
        "--suite-json",
        type=Path,
        default=None,
        metavar="PATH",
        help="用外部用例集 JSON 代替内置套件(单个套件对象或套件数组)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        metavar="PATH",
        help="报告输出路径(默认 reports/eval/<UTC 时间戳>.json)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        metavar="PATH",
        help="基线路径(默认仓库内 evals/baseline.json)",
    )
    parser.add_argument(
        "--update-baseline",
        nargs="?",
        const=DEFAULT_BASELINE,
        default=None,
        type=Path,
        metavar="PATH",
        help="用本次结果重写基线(缺省写仓库内的 evals/baseline.json)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        metavar="N",
        help="单用例 token 上限(超出即失败)",
    )
    parser.add_argument(
        "--max-total-tokens",
        type=int,
        default=None,
        metavar="N",
        help="本次运行 token 总上限(超出即失败)",
    )
    return parser


def _load_suites(path: Path | None) -> list[Suite]:
    if path is None:
        return build_suites()
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return suites_from_payload(payload, where=str(path))


def _select(suites: Sequence[Suite], names: Sequence[str] | None) -> list[Suite]:
    if not names:
        return list(suites)
    wanted = list(dict.fromkeys(names))
    found = {suite.name: suite for suite in suites}
    missing = [name for name in wanted if name not in found]
    if missing:
        raise ValueError(f"未知套件 {missing};可用套件:{sorted(found)}")
    return [found[name] for name in wanted]


def _apply_token_limits(
    results: Sequence[CaseResult], max_tokens: int | None
) -> list[str]:
    """单用例 token 阈值:超出记成该用例的失败原因。"""
    if max_tokens is None:
        return []
    messages: list[str] = []
    for result in results:
        if result.tokens <= max_tokens:
            continue
        message = f"token 用量 {result.tokens} 超过单用例上限 {max_tokens}"
        result.failures.append(message)
        result.ok = False
        messages.append(f"{result.suite}/{result.case_id} {message}")
    return messages


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        suites = _select(_load_suites(args.suite_json), args.suite)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"加载套件失败:{exc}", file=sys.stderr)
        return EXIT_FAILED

    results = run_suites(suites)
    limit_messages = _apply_token_limits(results, args.max_tokens)
    report = build_report(results)
    print_table(report)
    for message in limit_messages:
        print(f"阈值超限:{message}", file=sys.stderr)

    total_tokens = int(report["totals"]["tokens"])
    total_over = (
        args.max_total_tokens is not None and total_tokens > args.max_total_tokens
    )
    if total_over:
        print(
            f"阈值超限:本次 token 总量 {total_tokens} 超过上限 {args.max_total_tokens}",
            file=sys.stderr,
        )

    report_path = args.json or default_report_path()
    write_report(report_path, report)
    print(f"报告已写入 {report_path}")

    failed = bool(report["totals"]["failed"]) or total_over

    if args.update_baseline is not None:
        if failed:
            print("存在失败用例,拒绝更新基线(先修用例)", file=sys.stderr)
            return EXIT_FAILED
        write_report(args.update_baseline, baseline_payload(report))
        print(f"基线已更新 {args.update_baseline}")
        return EXIT_OK

    baseline_path = args.baseline or DEFAULT_BASELINE
    baseline = load_baseline(baseline_path)
    if baseline is None:
        print(
            f"基线不存在({baseline_path}),跳过 drift 比对;可用 --update-baseline 生成",
            file=sys.stderr,
        )
        return EXIT_FAILED if failed else EXIT_OK

    drift = compare(report, baseline, suites=args.suite)
    if failed:
        return EXIT_FAILED
    if drift:
        print("与基线相比出现 drift:", file=sys.stderr)
        for item in drift:
            print(f"  - {item}", file=sys.stderr)
        return EXIT_DRIFT
    print("与基线一致,无 drift")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
