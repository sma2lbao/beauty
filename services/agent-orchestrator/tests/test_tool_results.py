"""tool-results 统一判定(设计 §15.2):新信封 ok 优先,旧文本前缀兜底。"""

from __future__ import annotations

import json

from agent_orchestrator.graph.tool_results import is_tool_error


def test_new_success_envelope_is_ok() -> None:
    assert not is_tool_error(json.dumps({"ok": True, "content": "hello"}))


def test_new_failure_envelope_is_error() -> None:
    payload = {"ok": False, "error": {"code": "tool.not_found", "message": "x"}}
    assert is_tool_error(json.dumps(payload))


def test_ok_field_wins_over_error_key() -> None:
    """信封里 ok=true 即使带 error 字段也算成功(以 ok 为准)。"""

    assert not is_tool_error(json.dumps({"ok": True, "error": "ignored"}))


def test_legacy_error_key_without_ok_is_error() -> None:
    assert is_tool_error(json.dumps({"error": "旧工具错误文本"}))


def test_legacy_error_prefix_still_detected() -> None:
    """截断的旧 JSON(不是合法 JSON)仍按前缀判失败。"""

    assert is_tool_error('{"error": "未闭合')


def test_plain_text_is_success() -> None:
    assert not is_tool_error("文件内容如下")


def test_json_list_is_success() -> None:
    assert not is_tool_error("[1, 2, 3]")


def test_non_object_json_is_success() -> None:
    assert not is_tool_error('"just a string"')


def test_non_boolean_ok_falls_back_to_prefix_rule() -> None:
    """ok 不是布尔时不采信 ok,按旧规则(非前缀)判成功。"""

    assert not is_tool_error(json.dumps({"ok": "true"}))
