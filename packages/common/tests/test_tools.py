from __future__ import annotations

import pytest

from common.tools import (
    ExecutorKind,
    ToolDescriptor,
    ToolError,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolKind,
    ToolRisk,
    from_legacy_risk,
    to_legacy_risk,
    validate_arguments,
)


def _descriptor(**overrides: object) -> ToolDescriptor:
    payload: dict[str, object] = {
        "name": "read",
        "description": "读取文本文件",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "risk": ToolRisk.READ,
        "kind": ToolKind.FUNCTION,
        "executor": ExecutorKind.SANDBOX,
    }
    payload.update(overrides)
    return ToolDescriptor(**payload)  # type: ignore[arg-type]


class TestLegacyRisk:
    def test_roundtrip(self) -> None:
        for risk, legacy in (
            (ToolRisk.READ, "read"),
            (ToolRisk.WRITE, "write"),
            (ToolRisk.DESTRUCTIVE, "danger"),
        ):
            assert to_legacy_risk(risk) == legacy
            assert from_legacy_risk(legacy) is risk

    def test_unknown_legacy_is_destructive(self) -> None:
        assert from_legacy_risk("whatever") is ToolRisk.DESTRUCTIVE

    def test_to_legacy_rejects_non_risk(self) -> None:
        with pytest.raises(ValueError):
            to_legacy_risk("bogus")  # type: ignore[arg-type]


class TestToolDescriptor:
    def test_payload_roundtrip(self) -> None:
        descriptor = _descriptor(
            timeout_seconds=12.5,
            output_schema={"type": "object"},
            scopes=("files:read",),
            aliases=("legacy_tool_name",),
            enabled=False,
            version="3",
        )
        payload = descriptor.to_payload()
        assert payload["scopes"] == ["files:read"]
        assert payload["aliases"] == ["legacy_tool_name"]
        assert ToolDescriptor.from_payload(payload) == descriptor

    def test_defaults_and_minimal_payload(self) -> None:
        payload = _descriptor().to_payload()
        payload.pop("timeout_seconds")
        payload.pop("version")
        payload.pop("enabled")
        payload.pop("scopes")
        payload.pop("aliases")
        restored = ToolDescriptor.from_payload(payload)
        assert restored.timeout_seconds == 30.0
        assert restored.version == "1"
        assert restored.enabled is True
        assert restored.scopes == ()
        assert restored.aliases == ()

    def test_from_payload_rejects_malformed(self) -> None:
        with pytest.raises(ValueError):
            ToolDescriptor.from_payload([])  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            ToolDescriptor.from_payload({"description": "x"})
        with pytest.raises(ValueError):
            ToolDescriptor.from_payload(
                {"name": "read", "description": "x", "input_schema": "nope"}
            )
        payload = _descriptor().to_payload()
        payload["scopes"] = ["ok", 3]
        with pytest.raises(ValueError):
            ToolDescriptor.from_payload(payload)

    def test_from_payload_rejects_unknown_enums(self) -> None:
        payload = _descriptor().to_payload()
        payload["risk"] = "safe"
        with pytest.raises(ValueError):
            ToolDescriptor.from_payload(payload)


class TestToolExecuteRequest:
    def test_roundtrip_omits_none(self) -> None:
        request = ToolExecuteRequest(
            tool_call_id="call_1",
            arguments={"path": "a.py"},
            run_id="run_1",
            approved=True,
        )
        payload = request.to_payload()
        assert "conversation_id" not in payload
        assert "timeout_seconds" not in payload
        assert ToolExecuteRequest.from_payload(payload) == request

    def test_roundtrip_all_fields(self) -> None:
        request = ToolExecuteRequest(
            tool_call_id="call_2",
            arguments={},
            run_id="run_2",
            conversation_id="conv_1",
            workspace_id="ws_1",
            sandbox_scope="conv:conv_1",
            timeout_seconds=5.0,
        )
        assert ToolExecuteRequest.from_payload(request.to_payload()) == request

    def test_from_payload_rejects_malformed(self) -> None:
        with pytest.raises(ValueError):
            ToolExecuteRequest.from_payload({"arguments": {}})
        with pytest.raises(ValueError):
            ToolExecuteRequest.from_payload({"tool_call_id": "x", "arguments": []})
        with pytest.raises(ValueError):
            ToolExecuteRequest.from_payload(
                {"tool_call_id": "x", "arguments": {}, "run_id": 3}
            )


class TestToolExecuteResponse:
    def test_success_envelope_merges_result(self) -> None:
        response = ToolExecuteResponse(
            ok=True,
            result={"path": "a.py", "content": "print(1)"},
            truncated=True,
            duration_ms=12,
            tool_call_id="call_1",
        )
        envelope = response.envelope()
        assert envelope["ok"] is True
        assert envelope["path"] == "a.py"
        assert envelope["truncated"] is True
        assert envelope["duration_ms"] == 12
        assert "error" not in envelope

    def test_failure_envelope(self) -> None:
        response = ToolExecuteResponse(
            ok=False,
            error=ToolError(code="tool.not_found", message="不存在", retryable=False),
            tool_call_id="call_2",
        )
        envelope = response.envelope()
        assert envelope["ok"] is False
        assert envelope["error"] == {
            "code": "tool.not_found",
            "message": "不存在",
            "hint": "",
            "retryable": False,
        }

    def test_failure_without_error_uses_fallback(self) -> None:
        envelope = ToolExecuteResponse(ok=False).envelope()
        assert envelope["error"]["code"] == "tool.io_error"

    def test_from_payload_strips_meta_keys(self) -> None:
        envelope = ToolExecuteResponse(
            ok=True,
            result={"entries": []},
            truncated=False,
            duration_ms=7,
            tool_call_id="call_3",
            idempotent_replay=True,
        ).envelope()
        restored = ToolExecuteResponse.from_payload(envelope)
        assert restored.ok is True
        assert restored.result == {"entries": []}
        assert restored.duration_ms == 7
        assert restored.tool_call_id == "call_3"
        assert restored.idempotent_replay is True

    def test_from_payload_error_roundtrip(self) -> None:
        error = ToolError(code="tool.timeout", message="超时", retryable=True)
        envelope = ToolExecuteResponse(ok=False, error=error).envelope()
        restored = ToolExecuteResponse.from_payload(envelope)
        assert restored.ok is False
        assert restored.error == error
        assert restored.result is None

    def test_from_payload_rejects_envelope_without_ok(self) -> None:
        with pytest.raises(ValueError):
            ToolExecuteResponse.from_payload({"result": {}})
        with pytest.raises(ValueError):
            ToolExecuteResponse.from_payload({"ok": "true"})


SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1, "maxLength": 512},
        "offset": {"type": "integer", "minimum": 1, "maximum": 2000},
        "mode": {"type": "string", "enum": ["read", "write"]},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["path"],
    "additionalProperties": False,
}


class TestValidateArguments:
    def test_accepts_valid(self) -> None:
        assert (
            validate_arguments(
                SCHEMA,
                {"path": "a.py", "offset": 3, "mode": "read", "tags": ["x"]},
            )
            == []
        )

    def test_requires_object(self) -> None:
        errors = validate_arguments(SCHEMA, [])  # type: ignore[arg-type]
        assert errors[0].code == "tool.invalid_args"

    def test_missing_required(self) -> None:
        errors = validate_arguments(SCHEMA, {})
        assert [error.message for error in errors] == ["缺少必填参数 path"]

    def test_wrong_type(self) -> None:
        errors = validate_arguments(SCHEMA, {"path": 1})
        assert "期望类型 string" in errors[0].message
        assert errors[0].hint.endswith("后重试")

    def test_boolean_is_not_integer(self) -> None:
        errors = validate_arguments(SCHEMA, {"path": "a", "offset": True})
        assert "期望类型 integer" in errors[0].message

    def test_number_accepts_integer(self) -> None:
        schema = {"type": "object", "properties": {"ratio": {"type": "number"}}}
        assert validate_arguments(schema, {"ratio": 1}) == []

    def test_enum(self) -> None:
        errors = validate_arguments(SCHEMA, {"path": "a", "mode": "delete"})
        assert "必须是" in errors[0].message

    def test_length_bounds(self) -> None:
        assert validate_arguments(SCHEMA, {"path": ""}) != []
        assert validate_arguments(SCHEMA, {"path": "x" * 513}) != []

    def test_numeric_bounds(self) -> None:
        assert validate_arguments(SCHEMA, {"path": "a", "offset": 0}) != []
        assert validate_arguments(SCHEMA, {"path": "a", "offset": 2001}) != []

    def test_array_items_and_bounds(self) -> None:
        assert validate_arguments(SCHEMA, {"path": "a", "tags": ["x", 2]}) != []
        assert validate_arguments(SCHEMA, {"path": "a", "tags": ["a", "b", "c", "d"]})

    def test_additional_properties_rejected(self) -> None:
        errors = validate_arguments(SCHEMA, {"path": "a", "bash": "rm -rf /"})
        assert [error.message for error in errors] == ["不支持的参数 bash"]

    def test_pattern(self) -> None:
        schema = {
            "type": "object",
            "properties": {"sha": {"type": "string", "pattern": "^[0-9a-f]+$"}},
        }
        assert validate_arguments(schema, {"sha": "abc123"}) == []
        assert validate_arguments(schema, {"sha": "ABC"}) != []

    def test_bad_schema_pattern_reports_bad_pattern(self) -> None:
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string", "pattern": "("}},
        }
        errors = validate_arguments(schema, {"x": "y"})
        assert errors[0].code == "tool.bad_pattern"

    def test_deep_schema_is_bounded(self) -> None:
        schema: dict[str, object] = {"type": "object", "properties": {}}
        node = schema
        for _ in range(12):
            child: dict[str, object] = {"type": "object", "properties": {}}
            node["properties"] = {"next": child}  # type: ignore[assignment]
            node = child
        assert validate_arguments(schema, {"next": {"next": {}}}) == []

    def test_nested_object_error_path(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"question": {"type": "string"}},
                        "required": ["question"],
                    },
                }
            },
        }
        errors = validate_arguments(schema, {"questions": [{"header": "x"}]})
        assert errors[0].message == "缺少必填参数 question"
        assert errors[0].hint.endswith("后重试")
