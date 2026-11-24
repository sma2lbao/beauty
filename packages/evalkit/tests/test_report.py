"""报告渲染:同一份 payload 输出 JSON/JUnit/Markdown/HTML/终端。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from evalkit.report import (
    build_report,
    console_summary,
    report_to_html,
    report_to_junit,
    report_to_markdown,
    write_json,
    write_text,
)
from evalkit.runner import CaseRun, SuiteRun


def make_case(
    case_id: str,
    *,
    ok: bool = True,
    score: float = 1.0,
    tokens: int = 10,
    duration_ms: float = 10.0,
    error: str | None = None,
    failures: list[str] | None = None,
) -> CaseRun:
    return CaseRun(
        suite="demo",
        case_id=case_id,
        group="default",
        tags=(),
        ok=ok,
        score=score,
        duration_ms=duration_ms,
        output="out",
        error=error,
        outcomes=tuple(
            {
                "id": f"g{index}",
                "type": "equals",
                "ok": False,
                "required": True,
                "weight": 1.0,
                "message": message,
            }
            for index, message in enumerate(failures or (), start=1)
        ),
        fields={"tokens": tokens},
        events=(),
        tool_calls=(),
    )


@pytest.fixture()
def report() -> dict:
    run = SuiteRun(
        name="demo",
        dataset="dataset",
        dataset_version=1,
        cases=(
            make_case("demo/pass", duration_ms=8.0),
            make_case(
                "demo/fail",
                ok=False,
                score=0.5,
                tokens=30,
                duration_ms=1500.0,
                failures=["字段 output 期望 'ok',实际 'bad'"],
            ),
            make_case(
                "demo/error",
                ok=False,
                score=0.0,
                error="RuntimeError: boom",
                duration_ms=2.0,
            ),
        ),
        duration_ms=1510.0,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    return build_report([run], generated_at="2026-01-01T00:00:00+00:00")


def test_build_report_totals(report: dict) -> None:
    assert report["version"] == 1
    assert report["generated_at"] == "2026-01-01T00:00:00+00:00"
    totals = report["totals"]
    assert totals["cases"] == 3
    assert totals["passed"] == 1
    assert totals["failed"] == 2
    assert totals["pass_rate"] == pytest.approx(0.3333)
    assert totals["score"] == pytest.approx(0.5)
    assert totals["tokens"] == 50
    assert totals["duration_ms"] == 1510.0


def test_build_report_generates_time() -> None:
    payload = build_report([])
    assert payload["totals"]["cases"] == 0
    assert payload["generated_at"].endswith("+00:00")


def test_write_helpers_create_parents(tmp_path: Path, report: dict) -> None:
    json_path = write_json(tmp_path / "a" / "b" / "r.json", report)
    assert json.loads(json_path.read_text(encoding="utf-8"))["version"] == 1
    text_path = write_text(tmp_path / "a" / "b" / "r.md", "# hi")
    assert text_path.read_text(encoding="utf-8") == "# hi"


def test_console_summary(report: dict) -> None:
    text = console_summary(report)
    assert "套件" in text
    assert "demo" in text
    assert "33.3%" in text
    assert "50" in text
    assert "1.5s" in text
    assert "合计" in text
    assert "0.0%" in console_summary({})


def test_markdown_with_failures(report: dict) -> None:
    text = report_to_markdown(report)
    assert text.startswith("# 评测报告")
    assert "| demo | 3 | 1 | 33.3% | 0.500 | 50 | 1.5s |" in text
    assert "### demo / demo/fail" in text
    assert "字段 output" in text
    assert "执行错误:RuntimeError: boom" in text
    assert text.endswith("\n")


def test_markdown_escapes_pipes() -> None:
    run = SuiteRun(
        name="a|b",
        dataset="d",
        dataset_version=1,
        cases=(),
        duration_ms=0.0,
        generated_at="t",
    )
    text = report_to_markdown(build_report([run]))
    assert "| a\\|b |" in text
    assert "无。" in text


def test_markdown_no_failures() -> None:
    run = SuiteRun(
        name="demo",
        dataset="d",
        dataset_version=1,
        cases=(make_case("demo/ok"),),
        duration_ms=1.0,
        generated_at="t",
    )
    text = report_to_markdown(build_report([run]))
    assert "无。" in text


def test_junit_xml(report: dict) -> None:
    text = report_to_junit(report)
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(text)
    assert root.tag == "testsuites"
    assert root.attrib["tests"] == "3"
    assert root.attrib["failures"] == "2"
    assert root.attrib["time"] == "1.510"
    suite = root.find("testsuite")
    assert suite is not None
    assert suite.attrib["name"] == "demo"
    cases = suite.findall("testcase")
    assert [node.attrib["name"] for node in cases] == [
        "demo/pass",
        "demo/fail",
        "demo/error",
    ]
    assert cases[0].find("failure") is None
    fail = cases[1].find("failure")
    assert fail is not None
    assert fail.attrib["type"] == "assertion"
    assert "字段 output" in fail.attrib["message"]
    error = cases[2].find("failure")
    assert error is not None
    assert error.attrib["type"] == "error"


def test_html_with_failures(report: dict) -> None:
    text = report_to_html(report)
    assert "<!doctype html>" in text
    assert "评测报告" in text
    assert "33.3%" in text
    assert "demo / demo/fail" in text
    assert "字段 output" in text
    assert "没有失败用例" not in text


def test_html_no_failures() -> None:
    run = SuiteRun(
        name="demo",
        dataset="d",
        dataset_version=1,
        cases=(make_case("demo/ok"),),
        duration_ms=1.0,
        generated_at="t",
    )
    text = report_to_html(build_report([run]))
    assert "没有失败用例。" in text


def test_html_escapes_values() -> None:
    run = SuiteRun(
        name="<demo>",
        dataset="d",
        dataset_version=1,
        cases=(),
        duration_ms=0.0,
        generated_at="t",
    )
    text = report_to_html(build_report([run]))
    assert "&lt;demo&gt;" in text
    assert "<demo>" not in text
