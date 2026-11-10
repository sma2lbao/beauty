"""评测报告与基线比对(设计 §2.3)。

报告是稳定的 JSON 契约:version/generated_at/suites/totals,基线文件复用
同一形状但去掉 generated_at(时间戳不该造成 diff)。比对只做"退化信号":
用例增删、ok true→false、token 超基线 20%、事件集合增减、工具集合增减。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runner import CaseResult

REPORT_VERSION = 1
#: 基线路径(相对本文件定位,与 cwd 无关)。
DEFAULT_BASELINE = Path(__file__).resolve().parents[2] / "evals" / "baseline.json"
#: 仓库根:agent_orchestrator/evals/report.py -> up 4 层。
REPO_ROOT = Path(__file__).resolve().parents[4]
#: token 漂移阈值:超过基线 20% 视为退化。
TOKEN_DRIFT_RATIO = 0.2


def build_report(results: Sequence[CaseResult]) -> dict[str, Any]:
    """把用例结果聚合成报告(按套件分组,保持执行顺序)。"""
    suites: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for result in results:
        entry = index.get(result.suite)
        if entry is None:
            entry = {"name": result.suite, "cases": []}
            index[result.suite] = entry
            suites.append(entry)
        entry["cases"].append(result.to_dict())
    cases = [case for suite in suites for case in suite["cases"]]
    return {
        "version": REPORT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "suites": suites,
        "totals": {
            "cases": len(cases),
            "passed": sum(1 for case in cases if case["ok"]),
            "failed": sum(1 for case in cases if not case["ok"]),
            "tokens": sum(int(case.get("tokens") or 0) for case in cases),
        },
    }


def baseline_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """基线 = 报告去掉 generated_at。"""
    return {key: value for key, value in report.items() if key != "generated_at"}


def load_baseline(path: Path) -> dict[str, Any] | None:
    """读基线;文件不存在返回 None(调用方决定是 warning 还是报错)。"""
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else None


def write_report(path: Path, report: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def default_report_path(now: datetime | None = None) -> Path:
    """默认报告路径:reports/eval/<UTC 时间戳>.json(reports/ 已 gitignore)。"""
    moment = now or datetime.now(UTC)
    return REPO_ROOT / "reports" / "eval" / f"{moment.strftime('%Y%m%dT%H%M%SZ')}.json"


def _cases_by_id(report: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    found: dict[tuple[str, str], Mapping[str, Any]] = {}
    for suite in report.get("suites") or []:
        name = str(suite.get("name") or "")
        for case in suite.get("cases") or []:
            found[(name, str(case.get("id") or ""))] = case
    return found


def _event_set(case: Mapping[str, Any], key: str) -> set[str]:
    return {str(item) for item in case.get(key) or []}


def _only(
    cases: Mapping[tuple[str, str], Mapping[str, Any]],
    suites: Sequence[str] | None,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    if suites is None:
        return dict(cases)
    allowed = set(suites)
    return {key: value for key, value in cases.items() if key[0] in allowed}


def compare(
    report: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    suites: Sequence[str] | None = None,
) -> list[str]:
    """返回 drift 描述列表;空列表表示与基线一致。

    suites 非空时只比对这几套(配合 --suite 单套运行,避免其它套件的
    用例被误判为"删除")。
    """
    drift: list[str] = []
    current = _only(_cases_by_id(report), suites)
    previous = _only(_cases_by_id(baseline), suites)
    for suite, case_id in sorted(set(current) - set(previous)):
        drift.append(f"新增用例 {suite}/{case_id}")
    for suite, case_id in sorted(set(previous) - set(current)):
        drift.append(f"删除用例 {suite}/{case_id}")

    for key in sorted(set(current) & set(previous)):
        suite, case_id = key
        now = current[key]
        before = previous[key]
        label = f"{suite}/{case_id}"
        if before.get("ok") and not now.get("ok"):
            drift.append(f"{label} 由通过变为失败")
        base_tokens = int(before.get("tokens") or 0)
        current_tokens = int(now.get("tokens") or 0)
        if current_tokens > base_tokens * (1 + TOKEN_DRIFT_RATIO):
            drift.append(
                f"{label} token 用量 {current_tokens} 超过基线 {base_tokens} 的 20%"
            )
        for key_name, label_name in (
            ("events", "事件集合"),
            ("tool_calls", "工具调用集合"),
        ):
            added = _event_set(now, key_name) - _event_set(before, key_name)
            removed = _event_set(before, key_name) - _event_set(now, key_name)
            if added:
                drift.append(f"{label} {label_name}新增 {sorted(added)}")
            if removed:
                drift.append(f"{label} {label_name}缺少 {sorted(removed)}")
    return drift


def _pad(text: str, width: int) -> str:
    """按显示宽度左对齐(中文按 2 列计),表格才不歪。"""
    shown = sum(2 if ord(char) > 0x2E80 else 1 for char in text)
    return text + " " * max(0, width - shown)


def format_table(report: Mapping[str, Any]) -> str:
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for suite in report.get("suites") or []:
        for case in suite.get("cases") or []:
            rows.append(
                (
                    f"{suite.get('name')}/{case.get('id')}",
                    "PASS" if case.get("ok") else "FAIL",
                    f"{float(case.get('duration_ms') or 0):.0f}",
                    str(case.get("tokens") or 0),
                    str(len(case.get("events") or [])),
                    str(case.get("deliverables") or 0),
                    ";".join(str(item) for item in case.get("failures") or []),
                )
            )
    headers = ("用例", "结果", "耗时ms", "token", "事件", "交付物", "失败原因")
    widths = [
        max(
            [_display_width(headers[column])]
            + [_display_width(row[column]) for row in rows]
        )
        for column in range(len(headers))
    ]
    lines = ["  ".join(_pad(headers[i], widths[i]) for i in range(len(headers)))]
    for row in rows:
        lines.append(
            "  ".join(_pad(row[i], widths[i]) for i in range(len(headers))).rstrip()
        )
    totals = report.get("totals") or {}
    lines.append(
        f"合计:用例 {totals.get('cases', 0)},通过 {totals.get('passed', 0)},"
        f"失败 {totals.get('failed', 0)},token {totals.get('tokens', 0)}"
    )
    return "\n".join(lines)


def _display_width(text: str) -> int:
    return sum(2 if ord(char) > 0x2E80 else 1 for char in text)


def print_table(report: Mapping[str, Any]) -> None:
    print(format_table(report))
