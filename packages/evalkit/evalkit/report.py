"""报告渲染:JSON、JUnit XML、Markdown、自包含 HTML 与控制台摘要。

所有渲染只读 build_report 生成的同一份 payload,避免各格式口径漂移。
HTML 不依赖前端构建,CI 产物可直接下载打开。
"""

from __future__ import annotations

import html
import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runner import SuiteRun


def build_report(
    suite_runs: Sequence[SuiteRun],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """把所有套件结果聚合成一份报告 payload。"""
    totals = _totals(suite_runs)
    return {
        "version": 1,
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "totals": totals,
        "suites": [run.to_dict() for run in suite_runs],
    }


def write_json(path: str | Path, report: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def write_text(path: str | Path, content: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def console_summary(report: Mapping[str, Any]) -> str:
    """终端摘要表:套件维度,失败明细另由报告正文给出。"""
    header = (
        f"{'套件':<22}{'用例':>5}{'通过':>6}{'通过率':>8}"
        f"{'得分':>8}{'tokens':>9}{'耗时':>9}"
    )
    lines = [header, "-" * len(header)]
    for suite in _suites(report):
        totals = suite.get("totals") or {}
        lines.append(
            f"{str(suite.get('name', '')):<22}"
            f"{int(totals.get('cases', 0)):>5}"
            f"{int(totals.get('passed', 0)):>6}"
            f"{_percent(totals.get('pass_rate')):>8}"
            f"{float(totals.get('score', 0.0)):>8.3f}"
            f"{int(totals.get('tokens', 0)):>9}"
            f"{_duration(totals.get('duration_ms')):>9}"
        )
    total = report.get("totals") or {}
    lines.append("-" * len(header))
    lines.append(
        f"{'合计':<22}{int(total.get('cases', 0)):>5}"
        f"{int(total.get('passed', 0)):>6}"
        f"{_percent(total.get('pass_rate')):>8}"
        f"{float(total.get('score', 0.0)):>8.3f}"
        f"{int(total.get('tokens', 0)):>9}"
        f"{_duration(total.get('duration_ms')):>9}"
    )
    return "\n".join(lines)


def report_to_markdown(report: Mapping[str, Any]) -> str:
    total = report.get("totals") or {}
    lines = [
        "# 评测报告",
        "",
        f"- 生成时间:{report.get('generated_at', '')}",
        f"- 用例:{total.get('cases', 0)},"
        f"通过:{total.get('passed', 0)}({_percent(total.get('pass_rate'))})",
        f"- 平均得分:{float(total.get('score', 0.0)):.3f}",
        f"- 总 tokens:{total.get('tokens', 0)}",
        f"- 总耗时:{_duration(total.get('duration_ms'))}",
        "",
        "## 套件",
        "",
        "| 套件 | 用例 | 通过 | 通过率 | 平均分 | tokens | 耗时 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for suite in _suites(report):
        totals = suite.get("totals") or {}
        lines.append(
            f"| {_md(str(suite.get('name', '')))} "
            f"| {totals.get('cases', 0)} | {totals.get('passed', 0)} "
            f"| {_percent(totals.get('pass_rate'))} "
            f"| {float(totals.get('score', 0.0)):.3f} "
            f"| {totals.get('tokens', 0)} "
            f"| {_duration(totals.get('duration_ms'))} |"
        )
    failures = _failed_cases(report)
    lines.append("")
    lines.append("## 失败用例")
    lines.append("")
    if not failures:
        lines.append("无。")
    for suite_name, case in failures:
        lines.append(f"### {suite_name} / {case.get('id', '')}")
        lines.append("")
        for message in _case_failures(case):
            lines.append(f"- {message}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report_to_junit(report: Mapping[str, Any]) -> str:
    total = report.get("totals") or {}
    root = ET.Element(
        "testsuites",
        {
            "name": "evalkit",
            "tests": str(total.get("cases", 0)),
            "failures": str(total.get("failed", 0)),
            "time": _seconds(total.get("duration_ms")),
        },
    )
    for suite in _suites(report):
        suite_name = str(suite.get("name", ""))
        totals = suite.get("totals") or {}
        node = ET.SubElement(
            root,
            "testsuite",
            {
                "name": suite_name,
                "tests": str(totals.get("cases", 0)),
                "failures": str(totals.get("failed", 0)),
                "time": _seconds(totals.get("duration_ms")),
            },
        )
        for case in suite.get("cases") or []:
            if not isinstance(case, Mapping):
                continue
            case_node = ET.SubElement(
                node,
                "testcase",
                {
                    "classname": f"eval.{suite_name}",
                    "name": str(case.get("id", "")),
                    "time": _seconds(case.get("duration_ms")),
                },
            )
            if case.get("ok"):
                continue
            failures = _case_failures(case)
            failure = ET.SubElement(
                case_node,
                "failure",
                {
                    "type": "error" if case.get("error") else "assertion",
                    "message": failures[0] if failures else "未通过",
                },
            )
            failure.text = "\n".join(failures)
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def report_to_html(report: Mapping[str, Any]) -> str:
    total = report.get("totals") or {}
    rows = []
    for suite in _suites(report):
        totals = suite.get("totals") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(suite.get('name', '')))}</td>"
            f"<td>{int(totals.get('cases', 0))}</td>"
            f"<td>{int(totals.get('passed', 0))}</td>"
            f"<td>{html.escape(_percent(totals.get('pass_rate')))}</td>"
            f"<td>{float(totals.get('score', 0.0)):.3f}</td>"
            f"<td>{int(totals.get('tokens', 0))}</td>"
            f"<td>{html.escape(_duration(totals.get('duration_ms')))}</td>"
            "</tr>"
        )
    failure_blocks = []
    for suite_name, case in _failed_cases(report):
        messages = "".join(
            f"<li>{html.escape(message)}</li>" for message in _case_failures(case)
        )
        failure_blocks.append(
            "<details open>"
            f"<summary>{html.escape(suite_name)} / "
            f"{html.escape(str(case.get('id', '')))}</summary>"
            f"<ul>{messages}</ul></details>"
        )
    if not failure_blocks:
        failure_blocks.append('<p class="ok">没有失败用例。</p>')
    return _HTML_TEMPLATE.format(
        generated_at=html.escape(str(report.get("generated_at", ""))),
        cases=int(total.get("cases", 0)),
        passed=int(total.get("passed", 0)),
        pass_rate=html.escape(_percent(total.get("pass_rate"))),
        score=f"{float(total.get('score', 0.0)):.3f}",
        tokens=int(total.get("tokens", 0)),
        duration=html.escape(_duration(total.get("duration_ms"))),
        rows="\n".join(rows),
        failures="\n".join(failure_blocks),
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>评测报告</title>
<style>
:root {{ color-scheme: light; --ink: #17202a; --muted: #667085; --line: #e4e7ec; }}
body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--ink); margin: 0; padding: 32px; background: #f8fafc; }}
main {{ max-width: 1040px; margin: 0 auto; }}
h1 {{ margin: 0 0 4px; font-size: 24px; }}
.muted {{ color: var(--muted); margin: 0 0 24px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin-bottom: 24px; }}
.card {{ background: #fff; border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; }}
.card span {{ display: block; color: var(--muted); font-size: 12px; }}
.card strong {{ font-size: 22px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff;
  border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--line); }}
th {{ background: #f2f4f7; font-weight: 600; }}
tr:last-child td {{ border-bottom: 0; }}
section {{ margin-top: 28px; }}
details {{ background: #fff; border: 1px solid var(--line); border-radius: 8px;
  padding: 10px 14px; margin-bottom: 8px; }}
summary {{ cursor: pointer; font-weight: 600; }}
.ok {{ color: #067647; }}
</style>
</head>
<body>
<main>
<h1>评测报告</h1>
<p class="muted">生成时间:{generated_at}</p>
<div class="cards">
  <div class="card"><span>用例</span><strong>{cases}</strong></div>
  <div class="card"><span>通过</span><strong>{passed}</strong></div>
  <div class="card"><span>通过率</span><strong>{pass_rate}</strong></div>
  <div class="card"><span>平均得分</span><strong>{score}</strong></div>
  <div class="card"><span>tokens</span><strong>{tokens}</strong></div>
  <div class="card"><span>耗时</span><strong>{duration}</strong></div>
</div>
<table>
<thead><tr><th>套件</th><th>用例</th><th>通过</th><th>通过率</th>
<th>平均分</th><th>tokens</th><th>耗时</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
<section>
<h2>失败用例</h2>
{failures}
</section>
</main>
</body>
</html>
"""


def _totals(suite_runs: Sequence[SuiteRun]) -> dict[str, Any]:
    cases = sum(len(run.cases) for run in suite_runs)
    passed = sum(1 for run in suite_runs for case in run.cases if case.ok)
    score = (
        sum(case.score for run in suite_runs for case in run.cases) / cases
        if cases
        else 0.0
    )
    return {
        "cases": cases,
        "passed": passed,
        "failed": cases - passed,
        "pass_rate": round(passed / cases, 4) if cases else 0.0,
        "score": round(score, 4),
        "tokens": sum(case.tokens for run in suite_runs for case in run.cases),
        "duration_ms": round(sum(run.duration_ms for run in suite_runs), 2),
    }


def _suites(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    suites = report.get("suites")
    if not isinstance(suites, list):
        return []
    return [suite for suite in suites if isinstance(suite, Mapping)]


def _failed_cases(
    report: Mapping[str, Any],
) -> list[tuple[str, Mapping[str, Any]]]:
    failures: list[tuple[str, Mapping[str, Any]]] = []
    for suite in _suites(report):
        suite_name = str(suite.get("name", ""))
        cases = suite.get("cases")
        if not isinstance(cases, list):
            continue
        for case in cases:
            if isinstance(case, Mapping) and not case.get("ok"):
                failures.append((suite_name, case))
    return failures


def _case_failures(case: Mapping[str, Any]) -> list[str]:
    messages = case.get("failures")
    if isinstance(messages, list) and messages:
        return [str(message) for message in messages]
    if case.get("error"):
        return [f"执行错误:{case['error']}"]
    return ["未通过"]


def _percent(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "0.0%"
    return f"{float(value) * 100:.1f}%"


def _duration(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "0ms"
    milliseconds = float(value)
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.1f}s"
    return f"{milliseconds:.0f}ms"


def _seconds(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "0"
    return f"{float(value) / 1000:.3f}"


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
