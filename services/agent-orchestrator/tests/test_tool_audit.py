"""P1 工具审计:图节点 _record 与 run.tool.called.v1 payload 的富字段/体积上限。

覆盖设计 §11.1(审计字段)与 §15.3(单事件 ≤16KB):
- _record 从工具结果信封提取 error_code/truncated/paths/sha256,P0 字段语义不变;
- 事件 payload 始终含全部审计键,超长时按 arguments → paths → detail 收窄。
"""

from __future__ import annotations

import json

from agent_orchestrator.graph.nodes import _record
from agent_orchestrator.services.run_execution_approvals import (
    MAX_TOOL_EVENT_BYTES,
    _tool_event_payload,
)


def test_record_keeps_p0_fields_and_enriches() -> None:
    """成功信封:保留 name/arguments/ok/detail,并补全 P1 审计字段。"""
    output = json.dumps(
        {
            "ok": True,
            "path": "notes/a.txt",
            "paths": ["notes/b.txt", "notes/a.txt"],
            "truncated": True,
            "sha256_before": "a" * 64,
            "sha256_after": "b" * 64,
        }
    )
    record = _record(
        "write",
        '{"path": "notes/a.txt"}',
        output,
        tool_call_id="call-1",
        risk="danger",
        decision="approved",
        duration_ms=12,
    )

    assert record["name"] == "write"
    assert record["arguments"] == '{"path": "notes/a.txt"}'
    assert record["ok"] is True
    assert record["detail"] == output[:200]
    assert record["tool_call_id"] == "call-1"
    assert record["risk"] == "danger"
    assert record["decision"] == "approved"
    assert record["error_code"] is None
    assert record["duration_ms"] == 12
    assert record["result_chars"] == len(output)
    assert record["truncated"] is True
    assert record["paths"] == ["notes/a.txt", "notes/b.txt"]
    assert record["sha256_before"] == "a" * 64
    assert record["sha256_after"] == "b" * 64


def test_record_legacy_error_result() -> None:
    """旧 {"error": ...} 结果仍判失败,落统一兜底错误码,缺省按最严风险。"""
    output = '{"error": "boom"}'
    record = _record("read", "{}", output)

    assert record["ok"] is False
    assert record["detail"] == output
    assert record["error_code"] == "tool.error"
    assert record["risk"] == "danger"
    assert record["decision"] == "auto"
    assert record["duration_ms"] == 0
    assert record["result_chars"] == len(output)
    assert record["truncated"] is False
    assert record["paths"] == []
    assert record["sha256_before"] is None


def test_record_new_failure_envelope_code() -> None:
    payload = {"ok": False, "error": {"code": "tool.no_parent", "message": "x"}}
    record = _record("write", "{}", json.dumps(payload))

    assert record["ok"] is False
    assert record["error_code"] == "tool.no_parent"


def test_tool_event_payload_keeps_rich_fields() -> None:
    """正常大小的事件:审计字段全部平铺在 payload 顶层(snake_case)。"""
    record = {
        "name": "write",
        "arguments": '{"path": "a.txt", "content": "hi"}',
        "ok": True,
        "detail": '{"ok": true, "path": "a.txt"}',
        "tool_call_id": "call-1",
        "risk": "danger",
        "decision": "approved",
        "error_code": None,
        "duration_ms": 7,
        "result_chars": 42,
        "truncated": False,
        "paths": ["a.txt"],
        "sha256_before": "a" * 64,
        "sha256_after": "b" * 64,
    }
    payload = _tool_event_payload(record)

    assert payload["name"] == "write"
    assert payload["ok"] is True
    assert payload["tool_call_id"] == "call-1"
    assert payload["risk"] == "danger"
    assert payload["decision"] == "approved"
    assert payload["error_code"] is None
    assert payload["duration_ms"] == 7
    assert payload["result_chars"] == 42
    assert payload["truncated"] is False
    assert payload["paths"] == ["a.txt"]
    assert payload["sha256_before"] == "a" * 64
    assert payload["sha256_after"] == "b" * 64
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert size <= MAX_TOOL_EVENT_BYTES


def test_tool_event_payload_fits_16kb() -> None:
    """超长参数 + 长路径:payload 必须收窄到 16KB 内,sha 保持 64 位原值。"""
    record = {
        "name": "write",
        "arguments": json.dumps({"content": "x" * 30_000}),
        "ok": True,
        "detail": "y" * 5_000,
        "tool_call_id": "call-big",
        "risk": "write",
        "decision": "auto",
        "error_code": None,
        "duration_ms": 3,
        "result_chars": 30_000,
        "truncated": False,
        # 25 条、每条上限 512 个汉字(UTF-8 ~1.5KB):20 条即 ~30KB,必超上限
        "paths": [f"目录{i}/" + "长路径" * 170 for i in range(25)],
        "sha256_before": "a" * 100,
        "sha256_after": "b" * 100,
    }
    payload = _tool_event_payload(record)
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    assert size <= MAX_TOOL_EVENT_BYTES
    assert len(payload["arguments"]) <= 1_000
    assert len(payload["detail"]) <= 200
    assert len(payload["paths"]) < 20
    assert all(len(path) <= 512 for path in payload["paths"])
    assert payload["sha256_before"] == "a" * 64
    assert payload["sha256_after"] == "b" * 64
