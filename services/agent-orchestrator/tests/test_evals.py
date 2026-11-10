"""评测 harness 自测(P1-K M1,契约 docs/design/eval-and-cost-dashboard.md §2)。

覆盖:脚本化模型与零网络守卫、断言逐键、用例 schema 校验、三个内置套件
端到端、报告与基线比对、CLI 退出码(0/1/2)与 --suite/--suite-json/
--update-baseline 落盘。全部进程内、脚本化模型、零网络。
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.evals.__main__ import main as cli_main
from agent_orchestrator.evals.assertions import evaluate
from agent_orchestrator.evals.fakes import (
    NetworkGuard,
    ScriptedLLM,
    ScriptExhausted,
)
from agent_orchestrator.evals.report import (
    DEFAULT_BASELINE,
    REPO_ROOT,
    baseline_payload,
    build_report,
    compare,
    default_report_path,
    format_table,
    load_baseline,
    write_report,
)
from agent_orchestrator.evals.runner import CaseResult, run_case, run_suites
from agent_orchestrator.evals.suites import (
    KIND_CHAT,
    Case,
    build_suites,
    suite_from_dict,
    suites_from_payload,
)


class _Metrics:
    """满足 assertions.CaseMetrics 协议的最小指标对象。"""

    def __init__(
        self,
        *,
        run_status: str | None = None,
        events: list[str] | None = None,
        tool_calls: list[str] | None = None,
        deliverables: int = 0,
        tokens: int = 0,
        reply: str = "",
    ) -> None:
        self.run_status = run_status
        self.events = list(events or [])
        self.tool_calls = list(tool_calls or [])
        self.deliverables = deliverables
        self.tokens = tokens
        self.reply = reply


def _sample_report() -> dict[str, Any]:
    return build_report(
        [
            CaseResult(
                suite="s",
                case_id="c",
                ok=True,
                duration_ms=12.345,
                run_status="completed",
                events=["run.created.v1", "run.completed.v1"],
                tool_calls=["external_publish"],
                tokens=100,
                deliverables=1,
                reply="完成",
            )
        ]
    )


# --- 替身/守卫 -------------------------------------------------------------


def test_scripted_model_handles_three_script_forms() -> None:
    llm = ScriptedLLM(
        [
            "第一句正文",
            {
                "tool_calls": [
                    {"name": "external_publish", "arguments": {"title": "周报"}}
                ]
            },
        ]
    )

    async def scenario() -> tuple[Any, Any]:
        first = await llm.complete([{"role": "user", "content": "hi"}])
        second = await llm.complete([{"role": "user", "content": "again"}])
        return first, second

    first, second = asyncio.run(scenario())
    assert first.content == "第一句正文"
    assert second.content == ""
    assert second.tool_calls[0].name == "external_publish"
    assert json.loads(second.tool_calls[0].arguments) == {"title": "周报"}
    assert llm.tokens > 0
    assert len(llm.calls) == 2


def test_scripted_model_rejects_unknown_script_item() -> None:
    with pytest.raises(ValueError):
        ScriptedLLM([42])


def test_scripted_model_reports_script_exhaustion() -> None:
    llm = ScriptedLLM(["只有一条"])

    async def scenario() -> None:
        await llm.complete([{"role": "user", "content": "hi"}])
        await llm.complete([{"role": "user", "content": "again"}])

    with pytest.raises(ScriptExhausted) as excinfo:
        asyncio.run(scenario())
    assert "脚本不足" in str(excinfo.value)


@pytest.mark.enable_socket
def test_network_guard_blocks_inet_connect() -> None:
    with NetworkGuard() as guard:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(RuntimeError, match="网络连接"):
                sock.connect(("127.0.0.1", 9))
        finally:
            sock.close()
    assert guard.violations


# --- 断言 -----------------------------------------------------------------


def test_assertions_report_every_failure() -> None:
    metrics = _Metrics(
        run_status="completed",
        events=["run.created.v1"],
        tool_calls=["external_publish"],
        deliverables=1,
        tokens=10,
        reply="好的",
    )
    failures = evaluate(
        {
            "run_status": "failed",
            "events_include": ["run.completed.v1"],
            "events_exclude": ["run.created.v1"],
            "tool_calls": ["other_tool"],
            "reply_contains": "周报",
            "min_events": 2,
            "max_events": 0,
            "deliverables": 2,
            "max_total_tokens": 5,
        },
        metrics,
    )
    assert len(failures) == 9
    assert all(isinstance(item, str) for item in failures)


def test_assertions_pass_and_reject_non_integer_deliverables() -> None:
    metrics = _Metrics(reply="帮你整理日程")
    assert (
        evaluate(
            {"run_status": None, "reply_contains": "整理", "deliverables": 0}, metrics
        )
        == []
    )
    assert evaluate({"deliverables": True}, _Metrics(deliverables=1))
    assert evaluate({"deliverables": -1}, _Metrics(deliverables=0))


def test_assertions_accept_single_string_for_sets() -> None:
    assert (
        evaluate(
            {"events_include": "run.created.v1"}, _Metrics(events=["run.created.v1"])
        )
        == []
    )
    assert evaluate({"tool_calls": "external_publish"}, _Metrics()) != []


# --- 用例 schema -----------------------------------------------------------


def _case_payload() -> dict[str, Any]:
    return {
        "id": "hello",
        "kind": "chat",
        "goal": "你好",
        "llm_script": ["你好!"],
        "assert": {"reply_contains": "你好"},
    }


def test_case_parses_and_derives_default_steps() -> None:
    case = Case.from_dict(_case_payload())
    assert case.kind == KIND_CHAT
    assert case.resolved_steps() == ("create_conversation", "send_chat")
    assert case.resolved_title() == "hello"
    assert Case.from_dict({**_case_payload(), "kind": "task"}).resolved_steps() == (
        "create_run",
        "approve_plan",
        "confirm_all",
        "wait_terminal",
    )


def test_case_rejects_invalid_payloads() -> None:
    with pytest.raises(ValueError):
        Case.from_dict({**_case_payload(), "id": ""})
    with pytest.raises(ValueError):
        Case.from_dict({**_case_payload(), "goal": ""})
    with pytest.raises(ValueError):
        Case.from_dict({**_case_payload(), "kind": "unknown"})
    with pytest.raises(ValueError):
        Case.from_dict({**_case_payload(), "llm_script": "你好"})
    with pytest.raises(ValueError):
        Case.from_dict({**_case_payload(), "steps": ["land_on_moon"]})
    with pytest.raises(ValueError):
        Case.from_dict({**_case_payload(), "assert": {"evnts": []}})
    with pytest.raises(ValueError):
        Case.from_dict({**_case_payload(), "policies": [{"action": "auto"}]})


def test_suite_roundtrip_and_duplicate_ids() -> None:
    case = Case.from_dict(_case_payload())
    suite = suite_from_dict({"name": "mini", "cases": [case.to_dict()]})
    assert suite.name == "mini"
    assert suite.to_dict()["cases"][0]["assert"] == {"reply_contains": "你好"}
    assert [item.name for item in suites_from_payload([suite.to_dict()])] == ["mini"]
    with pytest.raises(ValueError):
        suite_from_dict({"name": "mini", "cases": [case.to_dict(), case.to_dict()]})
    with pytest.raises(ValueError):
        suites_from_payload("不是套件")


def test_builtin_suites_cover_three_scenarios() -> None:
    suites = build_suites()
    assert [suite.name for suite in suites] == [
        "chat-direct",
        "task-approval",
        "tool-policy",
    ]
    assert all(suite.cases for suite in suites)
    assert suites[2].cases[0].policies


# --- 端到端 ---------------------------------------------------------------


def test_builtin_suites_pass_end_to_end() -> None:
    results = run_suites(build_suites())
    assert len(results) == 3
    assert all(result.ok for result in results), [
        (result.case_id, result.failures) for result in results if not result.ok
    ]
    assert sum(result.tokens for result in results) > 0
    assert all(result.duration_ms >= 0 for result in results)


def test_run_case_reports_script_exhaustion() -> None:
    case = Case(
        id="short",
        kind=KIND_CHAT,
        goal="你好",
        llm_script=(),
        expect={"reply_contains": "你好"},
    )
    result = run_case("chat-direct", case)
    assert not result.ok
    assert result.failures


def test_run_case_reports_assertion_failure() -> None:
    case = Case(
        id="wrong-answer",
        kind=KIND_CHAT,
        goal="你好",
        llm_script=("今天天气不错。",),
        expect={"reply_contains": "整理本周日程"},
    )
    result = run_case("chat-direct", case)
    assert not result.ok
    assert any("回复不包含" in item for item in result.failures)


# --- 报告/基线 ------------------------------------------------------------


def test_report_shape_and_totals() -> None:
    report = _sample_report()
    assert report["version"] == 1
    assert report["generated_at"]
    assert report["totals"] == {
        "cases": 1,
        "passed": 1,
        "failed": 0,
        "tokens": 100,
    }
    case = report["suites"][0]["cases"][0]
    assert case["id"] == "c"
    assert case["duration_ms"] == 12.35
    assert "generated_at" not in baseline_payload(report)


def test_compare_detects_every_drift_kind() -> None:
    baseline = build_report(
        [
            CaseResult(
                suite="s",
                case_id="c",
                ok=True,
                tokens=100,
                events=["a", "b"],
                tool_calls=["t"],
            ),
            CaseResult(suite="s", case_id="gone", ok=True),
        ]
    )
    current = build_report(
        [
            CaseResult(
                suite="s",
                case_id="c",
                ok=False,
                tokens=150,
                events=["a"],
                tool_calls=["t", "x"],
            ),
            CaseResult(suite="s", case_id="new", ok=False),
        ]
    )
    joined = "\n".join(compare(current, baseline))
    assert "新增用例 s/new" in joined
    assert "删除用例 s/gone" in joined
    assert "由通过变为失败" in joined
    assert "token 用量" in joined
    assert "事件集合缺少" in joined
    assert "工具调用集合新增" in joined
    assert compare(current, baseline, suites=["other"]) == []


def test_baseline_roundtrip_and_table(tmp_path: Path) -> None:
    report = _sample_report()
    path = tmp_path / "nested" / "baseline.json"
    assert load_baseline(path) is None
    write_report(path, baseline_payload(report))
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded["totals"]["cases"] == 1
    assert "generated_at" not in loaded
    table = format_table(report)
    assert "s/c" in table and "PASS" in table and "合计" in table


def test_default_report_path_is_under_reports_dir() -> None:
    path = default_report_path()
    assert path.parent == REPO_ROOT / "reports" / "eval"
    assert path.suffix == ".json"


# --- CLI ------------------------------------------------------------------


@pytest.mark.skipif(
    not DEFAULT_BASELINE.exists(), reason="需要先 --update-baseline 生成基线"
)
def test_cli_passes_against_committed_baseline(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    assert cli_main(["--json", str(report_path)]) == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["totals"]["failed"] == 0
    assert payload["totals"]["passed"] == 3


@pytest.mark.skipif(
    not DEFAULT_BASELINE.exists(), reason="需要先 --update-baseline 生成基线"
)
def test_cli_returns_drift_exit_code(tmp_path: Path) -> None:
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
    baseline["suites"][0]["cases"][0]["tokens"] = 0
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    assert (
        cli_main(
            [
                "--suite",
                "chat-direct",
                "--baseline",
                str(baseline_path),
                "--json",
                str(tmp_path / "report.json"),
            ]
        )
        == 2
    )


def test_cli_update_baseline_writes_payload(tmp_path: Path) -> None:
    target = tmp_path / "baseline.json"
    assert (
        cli_main(
            [
                "--suite",
                "chat-direct",
                "--update-baseline",
                str(target),
                "--json",
                str(tmp_path / "report.json"),
            ]
        )
        == 0
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "generated_at" not in payload
    assert payload["suites"][0]["name"] == "chat-direct"


def test_cli_suite_json_and_unknown_suite(tmp_path: Path) -> None:
    external = tmp_path / "suite.json"
    external.write_text(
        json.dumps({"name": "mini", "cases": [_case_payload()]}),
        encoding="utf-8",
    )
    assert (
        cli_main(
            [
                "--suite-json",
                str(external),
                "--baseline",
                str(tmp_path / "missing.json"),
                "--json",
                str(tmp_path / "report.json"),
            ]
        )
        == 0
    )
    assert cli_main(["--suite", "nope", "--json", str(tmp_path / "x.json")]) == 1
    assert (
        cli_main(
            [
                "--suite-json",
                str(tmp_path / "nope.json"),
                "--json",
                str(tmp_path / "y.json"),
            ]
        )
        == 1
    )


def test_cli_token_limits_fail(tmp_path: Path) -> None:
    assert (
        cli_main(
            [
                "--suite",
                "chat-direct",
                "--max-tokens",
                "1",
                "--json",
                str(tmp_path / "report.json"),
            ]
        )
        == 1
    )
    assert (
        cli_main(
            [
                "--suite",
                "chat-direct",
                "--max-total-tokens",
                "1",
                "--json",
                str(tmp_path / "report2.json"),
            ]
        )
        == 1
    )
