"""evalkit 命令行:run / list / validate / version。

退出码与 CI 契约:
- 0:全部通过,且无基线漂移;
- 1:有用例失败或未达到 fail_under / token 上限;
- 2:仅基线漂移(人工确认后 --update-baseline);
- 3:配置、schema 或 adapter 加载错误。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .baseline import Drift, compare, load_baseline, write_baseline
from .config import PlatformConfig, SuiteConfig, load_config, select_suites
from .errors import EvalkitError
from .report import (
    build_report,
    console_summary,
    report_to_html,
    report_to_junit,
    report_to_markdown,
    write_json,
    write_text,
)
from .runner import SuiteRun, run_suites
from .schema import load_dataset

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_DRIFT = 2
EXIT_ERROR = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evalkit",
        description="离线 Agent/LLM 评测平台:数据集 + adapter + grader + 基线",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="执行评测并生成报告")
    _add_config(run_parser)
    run_parser.add_argument(
        "--suite", action="append", default=[], metavar="NAME", help="只跑指定套件"
    )
    run_parser.add_argument(
        "--tag", action="append", default=[], metavar="TAG", help="只跑带该标签的用例"
    )
    run_parser.add_argument(
        "--case", action="append", default=[], metavar="ID", help="只跑指定用例 id"
    )
    run_parser.add_argument("--json", type=Path, help="JSON 报告路径")
    run_parser.add_argument("--junit", type=Path, help="JUnit XML 路径")
    run_parser.add_argument("--markdown", type=Path, help="Markdown 路径")
    run_parser.add_argument("--html", type=Path, help="HTML 路径")
    run_parser.add_argument("--baseline", type=Path, help="覆盖基线路径(单套件)")
    run_parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="本次全部通过时写回基线",
    )
    run_parser.add_argument("--no-baseline", action="store_true", help="跳过基线比对")
    run_parser.add_argument(
        "--fail-under", type=float, help="全局通过率下限(覆盖套件配置)"
    )
    run_parser.add_argument(
        "--max-total-tokens", type=int, help="本次运行总 token 上限"
    )
    run_parser.add_argument("--quiet", action="store_true", help="不打印终端摘要")

    list_parser = subparsers.add_parser("list", help="列出套件与用例")
    _add_config(list_parser)
    list_parser.add_argument("--suite", action="append", default=[], metavar="NAME")
    list_parser.add_argument("--cases", action="store_true", help="同时列出用例 id")

    validate_parser = subparsers.add_parser("validate", help="校验配置与数据集")
    _add_config(validate_parser)

    subparsers.add_parser("version", help="打印版本")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "version":
            print(f"evalkit {__version__}")
            return EXIT_OK
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "validate":
            return _cmd_validate(args)
        parser.error(f"未知命令:{args.command}")
    except EvalkitError as exc:
        print(f"错误:{exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_ERROR


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    suites = select_suites(config, tuple(args.suite))
    if args.baseline is not None and len(suites) != 1:
        raise EvalkitError("--baseline 只能与单个 --suite 一起使用")
    if args.fail_under is not None and not 0 <= args.fail_under <= 1:
        raise EvalkitError("--fail-under 必须在 0~1")
    runs = list(
        run_suites(
            config,
            suite_names=tuple(args.suite),
            tags=tuple(args.tag),
            case_ids=tuple(args.case),
        )
    )
    report = build_report(runs)
    paths = _write_reports(report, config, args)
    if not args.quiet:
        print(console_summary(report))
        print()
        for label, path in paths.items():
            print(f"{label}: {path}")

    failures = _collect_failures(runs)
    threshold_failures = _threshold_failures(runs, suites, args.fail_under)
    token_failure = _token_failure(report, args.max_total_tokens)
    failures.extend(threshold_failures)
    if token_failure:
        failures.append(token_failure)

    if args.update_baseline:
        if failures:
            for message in failures:
                print(f"失败:{message}", file=sys.stderr)
            print("存在失败,拒绝更新基线。", file=sys.stderr)
            return EXIT_FAIL
        for suite, run in zip(suites, runs, strict=True):
            target = args.baseline or suite.baseline
            write_baseline(target, [run])
            print(f"基线已更新:{target}")
        return EXIT_OK

    drifts: list[Drift] = []
    if not args.no_baseline:
        for suite, run in zip(suites, runs, strict=True):
            target = args.baseline or suite.baseline
            if not target.is_file():
                print(
                    f"提示:基线不存在 {target};本次跳过比对"
                    "(首次可用 --update-baseline 生成)",
                    file=sys.stderr,
                )
                continue
            baseline = load_baseline(target)
            drifts.extend(compare(build_report([run]), baseline))

    for message in failures:
        print(f"失败:{message}", file=sys.stderr)
    for drift in drifts:
        print(f"漂移:[{drift.suite}/{drift.case_id}] {drift.message}", file=sys.stderr)
    if failures:
        return EXIT_FAIL
    if drifts:
        return EXIT_DRIFT
    return EXIT_OK


def _cmd_list(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    suites = select_suites(config, tuple(args.suite))
    for suite in suites:
        dataset = load_dataset(suite.dataset)
        print(
            f"{suite.name}: dataset={dataset.manifest.name} "
            f"v{dataset.manifest.version} adapter="
            f"{suite.adapter or dataset.manifest.adapter} cases={len(dataset.cases)}"
        )
        if args.cases:
            for case in dataset.cases:
                tags = f" tags={','.join(case.tags)}" if case.tags else ""
                print(f"  - {case.id}{tags}")
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    total = 0
    for suite in config.suites:
        dataset = load_dataset(suite.dataset)
        total += len(dataset.cases)
        print(f"OK {suite.name}: {len(dataset.cases)} 条用例")
    print(f"配置与数据集校验通过:{len(config.suites)} 个套件,{total} 条用例")
    return EXIT_OK


def _write_reports(
    report: Mapping[str, Any],
    config: PlatformConfig,
    args: argparse.Namespace,
) -> dict[str, Path]:
    if args.json is not None:
        json_path = args.json
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        json_path = config.report_dir / f"{stamp}.json"
    prefix = json_path.with_suffix("")
    junit_path = args.junit or prefix.with_suffix(".junit.xml")
    markdown_path = args.markdown or prefix.with_suffix(".md")
    html_path = args.html or prefix.with_suffix(".html")
    write_json(json_path, report)
    write_text(junit_path, report_to_junit(report))
    write_text(markdown_path, report_to_markdown(report))
    write_text(html_path, report_to_html(report))
    return {
        "JSON": json_path,
        "JUnit": junit_path,
        "Markdown": markdown_path,
        "HTML": html_path,
    }


def _collect_failures(runs: Sequence[SuiteRun]) -> list[str]:
    failures: list[str] = []
    for run in runs:
        for case in run.cases:
            if case.ok:
                continue
            reasons = "; ".join(case.failures()) or "未通过"
            failures.append(f"{run.name}/{case.case_id}: {reasons}")
    return failures


def _threshold_failures(
    runs: Sequence[SuiteRun],
    suites: Sequence[SuiteConfig],
    global_limit: float | None,
) -> list[str]:
    failures: list[str] = []
    for suite, run in zip(suites, runs, strict=True):
        limit = global_limit if global_limit is not None else suite.fail_under
        if limit is None:
            continue
        rate = run.totals["pass_rate"]
        if rate < limit:
            failures.append(f"{suite.name}: 通过率 {rate:.2%} 低于下限 {limit:.2%}")
    return failures


def _token_failure(report: Mapping[str, Any], limit: int | None) -> str | None:
    if limit is None:
        return None
    tokens = int((report.get("totals") or {}).get("tokens", 0))
    if tokens > limit:
        return f"总 token {tokens} 超过上限 {limit}"
    return None


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="平台配置路径(默认从 cwd 向上找 evals/evalkit.toml)",
    )
