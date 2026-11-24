"""工具调用详情采集:名称、参数、风险与决策都要可断言。"""

from __future__ import annotations

from agent_orchestrator.evals.runner import _tool_call_detail, run_case
from agent_orchestrator.evals.suites import Case


def test_tool_call_detail_parses_json_arguments() -> None:
    detail = _tool_call_detail(
        {
            "name": "external_publish",
            "arguments": '{"title": "周报"}',
            "ok": True,
            "risk": "danger",
            "decision": "approved",
            "duration_ms": 12,
        }
    )
    assert detail["name"] == "external_publish"
    assert detail["arguments"] == {"title": "周报"}
    assert detail["ok"] is True
    assert detail["risk"] == "danger"
    assert detail["decision"] == "approved"
    assert detail["duration_ms"] == 12
    assert detail["error_code"] is None


def test_tool_call_detail_keeps_unparsed_arguments() -> None:
    detail = _tool_call_detail(
        {"name": "external_publish", "arguments": "{not-json", "duration_ms": 7}
    )
    assert detail["arguments"] == "{not-json"
    assert detail["ok"] is False
    assert detail["risk"] == ""
    assert detail["decision"] == ""
    assert detail["duration_ms"] == 7


def test_run_case_records_tool_call_details() -> None:
    case = Case.from_dict(
        {
            "id": "tool-arguments/publish-title",
            "kind": "task",
            "goal": "整理本周日程并发布周报文档",
            "llm_script": [
                "# 执行计划\n- [ ] 检索日程\n- [ ] 发布说明",
                {
                    "tool_calls": [
                        {
                            "name": "external_publish",
                            "arguments": {"title": "周报"},
                        }
                    ]
                },
                "已发布周报。",
                "# 交付物\n\n周报已发布。",
            ],
            "steps": [
                "create_run",
                "approve_plan",
                "confirm_all",
                "wait_terminal",
            ],
        }
    )
    result = run_case("tool-arguments", case)
    assert result.ok, result.failures
    assert result.tool_calls == ["external_publish"]
    assert len(result.tool_call_details) == 1
    detail = result.tool_call_details[0]
    assert detail["name"] == "external_publish"
    assert detail["arguments"] == {"title": "周报"}
    assert detail["ok"] is True
    assert detail["risk"] == "danger"
    assert detail["decision"] == "approved"
    payload = result.to_dict()
    assert payload["tool_call_details"] == result.tool_call_details
