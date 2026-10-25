"""内置工具服务的 wire 契约（设计见 docs/design/tool-service.md §6）。

约定：

- 这里是唯一的线协议真源：tool-service 的 catalog、orchestrator 的 ToolClient、
  桌面端本地模式共用同一套 DTO；
- 传输格式是普通 dict（JSON），DTO 用 dataclass(frozen=True, slots=True) 并提供
  to_payload()/from_payload()——不引入 pydantic 版本耦合，也不强迫调用方构造对象；
- 工具执行错误是模型可见的数据而不是 API 失败：成功信封为
  {"ok": true, ...结果顶层平铺..., "truncated", "duration_ms", ...}，
  失败信封为 {"ok": false, "error": {"code", "message", "hint", "retryable"}}；
- 风险三档 ToolRisk 与 orchestrator 旧枚举（read/write/danger）通过
  to_legacy_risk()/from_legacy_risk() 互转，迁移期两套并存。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "ExecutorKind",
    "ToolDescriptor",
    "ToolError",
    "ToolExecuteRequest",
    "ToolExecuteResponse",
    "ToolKind",
    "ToolRisk",
    "from_legacy_risk",
    "to_legacy_risk",
    "validate_arguments",
]

_MAX_SCHEMA_DEPTH = 8


class ToolRisk(StrEnum):
    """工具风险三档：只读、写、不可逆。"""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ToolKind(StrEnum):
    """工具形态：普通执行 vs 需要用户输入的交互。"""

    FUNCTION = "function"
    INTERACTION = "interaction"


class ExecutorKind(StrEnum):
    """执行后端：沙箱内执行、代理到下游服务、服务内完成。"""

    SANDBOX = "sandbox"
    HTTP = "http"
    NONE = "none"


_LEGACY_RISK: dict[ToolRisk, str] = {
    ToolRisk.READ: "read",
    ToolRisk.WRITE: "write",
    ToolRisk.DESTRUCTIVE: "danger",
}
_FROM_LEGACY_RISK: dict[str, ToolRisk] = {
    legacy: risk for risk, legacy in _LEGACY_RISK.items()
}


def to_legacy_risk(risk: ToolRisk) -> str:
    """把三档风险映射成 orchestrator 旧枚举值（read/write/danger）。"""
    try:
        return _LEGACY_RISK[ToolRisk(risk)]
    except (KeyError, ValueError) as exc:  # pragma: no cover - 调用方传错类型
        raise ValueError(f"未知工具风险: {risk!r}") from exc


def from_legacy_risk(value: str) -> ToolRisk:
    """把旧枚举值映射成三档风险；未知值按 DESTRUCTIVE 处理（失败安全）。"""
    return _FROM_LEGACY_RISK.get(value, ToolRisk.DESTRUCTIVE)


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """工具的静态描述：目录、schema、风险、执行方式与限额。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    risk: ToolRisk
    kind: ToolKind
    executor: ExecutorKind
    timeout_seconds: float = 30.0
    output_schema: dict[str, Any] | None = None
    scopes: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    enabled: bool = True
    version: str = "1"

    def to_payload(self) -> dict[str, Any]:
        """序列化为 wire dict。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "risk": str(self.risk),
            "kind": str(self.kind),
            "executor": str(self.executor),
            "timeout_seconds": self.timeout_seconds,
            "scopes": list(self.scopes),
            "aliases": list(self.aliases),
            "enabled": self.enabled,
            "version": self.version,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ToolDescriptor:
        """从 wire dict 还原；字段缺失或非法抛 ValueError。"""
        if not isinstance(payload, dict):
            raise ValueError("ToolDescriptor 载荷必须是对象")
        input_schema = payload.get("input_schema")
        if not isinstance(input_schema, dict):
            raise ValueError("ToolDescriptor.input_schema 必须是对象")
        output_schema = payload.get("output_schema")
        if output_schema is not None and not isinstance(output_schema, dict):
            raise ValueError("ToolDescriptor.output_schema 必须是对象或 None")
        return cls(
            name=_require_str(payload, "name"),
            description=_require_str(payload, "description"),
            input_schema=input_schema,
            output_schema=output_schema,
            risk=ToolRisk(_require_str(payload, "risk")),
            kind=ToolKind(_require_str(payload, "kind")),
            executor=ExecutorKind(_require_str(payload, "executor")),
            timeout_seconds=float(payload.get("timeout_seconds", 30.0)),
            scopes=_tuple_of_str(payload.get("scopes", ()), "scopes"),
            aliases=_tuple_of_str(payload.get("aliases", ()), "aliases"),
            enabled=bool(payload.get("enabled", True)),
            version=str(payload.get("version", "1")),
        )


@dataclass(frozen=True, slots=True)
class ToolExecuteRequest:
    """一次工具执行请求；tool_call_id 是幂等键。"""

    tool_call_id: str
    arguments: dict[str, Any]
    run_id: str | None = None
    conversation_id: str | None = None
    workspace_id: str | None = None
    sandbox_scope: str | None = None
    timeout_seconds: float | None = None
    approved: bool = False

    def to_payload(self) -> dict[str, Any]:
        """序列化为 wire dict（省略值为 None 的可选字段）。"""
        payload: dict[str, Any] = {
            "tool_call_id": self.tool_call_id,
            "arguments": self.arguments,
            "approved": self.approved,
        }
        for key in (
            "run_id",
            "conversation_id",
            "workspace_id",
            "sandbox_scope",
            "timeout_seconds",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ToolExecuteRequest:
        """从 wire dict 还原；未知字段直接忽略。"""
        if not isinstance(payload, dict):
            raise ValueError("ToolExecuteRequest 载荷必须是对象")
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("ToolExecuteRequest.arguments 必须是对象")
        timeout = payload.get("timeout_seconds")
        return cls(
            tool_call_id=_require_str(payload, "tool_call_id"),
            arguments=arguments,
            run_id=_opt_str(payload, "run_id"),
            conversation_id=_opt_str(payload, "conversation_id"),
            workspace_id=_opt_str(payload, "workspace_id"),
            sandbox_scope=_opt_str(payload, "sandbox_scope"),
            timeout_seconds=float(timeout) if timeout is not None else None,
            approved=bool(payload.get("approved", False)),
        )


@dataclass(frozen=True, slots=True)
class ToolError:
    """工具错误的结构化描述（对外错误码见设计 §6.4）。"""

    code: str
    message: str
    hint: str = ""
    retryable: bool = False

    def to_payload(self) -> dict[str, Any]:
        """序列化为 wire dict。"""
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ToolError:
        """从 wire dict 还原。"""
        if not isinstance(payload, dict):
            raise ValueError("ToolError 载荷必须是对象")
        return cls(
            code=_require_str(payload, "code"),
            message=_require_str(payload, "message"),
            hint=str(payload.get("hint", "")),
            retryable=bool(payload.get("retryable", False)),
        )


_ENVELOPE_META_KEYS = frozenset(
    {"ok", "error", "truncated", "duration_ms", "tool_call_id", "idempotent_replay"}
)


@dataclass(frozen=True, slots=True)
class ToolExecuteResponse:
    """一次工具执行的结果信封（成功时结果顶层平铺）。"""

    ok: bool
    result: dict[str, Any] | None = None
    error: ToolError | None = None
    truncated: bool = False
    duration_ms: int = 0
    tool_call_id: str = ""
    idempotent_replay: bool = False

    def envelope(self) -> dict[str, Any]:
        """渲染 wire 信封：成功 {"ok": true, ...}，失败 {"ok": false, "error": ...}。"""
        payload: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            if self.result:
                payload.update(self.result)
            payload["truncated"] = self.truncated
        else:
            error = self.error or ToolError(
                code="tool.io_error", message="工具执行失败（未提供错误详情）"
            )
            payload["error"] = error.to_payload()
        payload["duration_ms"] = self.duration_ms
        payload["tool_call_id"] = self.tool_call_id
        payload["idempotent_replay"] = self.idempotent_replay
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ToolExecuteResponse:
        """从 wire 信封还原；ok 缺失或非布尔抛 ValueError。"""
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ValueError("工具响应信封必须包含布尔字段 ok")
        ok = bool(payload["ok"])
        error = None
        if not ok:
            raw_error = payload.get("error")
            error = ToolError.from_payload(raw_error) if raw_error else None
        return cls(
            ok=ok,
            result=(
                {k: v for k, v in payload.items() if k not in _ENVELOPE_META_KEYS}
                if ok
                else None
            ),
            error=error,
            truncated=bool(payload.get("truncated", False)),
            duration_ms=int(payload.get("duration_ms", 0)),
            tool_call_id=str(payload.get("tool_call_id", "")),
            idempotent_replay=bool(payload.get("idempotent_replay", False)),
        )


def validate_arguments(schema: dict[str, Any], args: dict[str, Any]) -> list[ToolError]:
    """按 JSON Schema 子集校验参数（不做类型强转，不填默认值）。

    支持的子集：type/object/array/string/number/integer/boolean/required/
    properties/additionalProperties/enum/minLength/maxLength/minimum/maximum/
    minItems/maxItems/pattern。返回结构化错误列表，空列表表示通过。
    """
    if not isinstance(args, dict):
        return [_invalid("参数必须是 JSON 对象", path="$")]
    errors: list[ToolError] = []
    _validate_node(schema, args, path="$", errors=errors, depth=0)
    return errors


def _validate_node(
    schema: Any,
    value: Any,
    *,
    path: str,
    errors: list[ToolError],
    depth: int,
) -> None:
    if not isinstance(schema, dict) or depth > _MAX_SCHEMA_DEPTH:
        return
    expected = schema.get("type")
    if expected is not None and not _matches_type(expected, value):
        errors.append(
            _invalid(f"{path} 期望类型 {expected}，实际 {_json_type(value)}", path=path)
        )
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(_invalid(f"{path} 必须是 {schema['enum']} 之一", path=path))
    if isinstance(value, str):
        _validate_str(schema, value, path=path, errors=errors)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(schema, value, path=path, errors=errors)
    elif isinstance(value, list):
        _validate_array(schema, value, path=path, errors=errors, depth=depth)
    elif isinstance(value, dict):
        _validate_object(schema, value, path=path, errors=errors, depth=depth)


def _validate_str(
    schema: dict[str, Any], value: str, *, path: str, errors: list[ToolError]
) -> None:
    min_len = schema.get("minLength")
    if isinstance(min_len, int) and len(value) < min_len:
        errors.append(_invalid(f"{path} 长度不能小于 {min_len}", path=path))
    max_len = schema.get("maxLength")
    if isinstance(max_len, int) and len(value) > max_len:
        errors.append(_invalid(f"{path} 长度不能大于 {max_len}", path=path))
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            matched = re.search(pattern, value) is not None
        except re.error:
            errors.append(
                ToolError(
                    code="tool.bad_pattern",
                    message=f"{path} 的 pattern 不是合法正则",
                    hint="这是工具 schema 的问题，请勿重试同一请求",
                )
            )
            return
        if not matched:
            errors.append(_invalid(f"{path} 不匹配模式 {pattern}", path=path))


def _validate_number(
    schema: dict[str, Any], value: float, *, path: str, errors: list[ToolError]
) -> None:
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and value < minimum:
        errors.append(_invalid(f"{path} 不能小于 {minimum}", path=path))
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and value > maximum:
        errors.append(_invalid(f"{path} 不能大于 {maximum}", path=path))


def _validate_array(
    schema: dict[str, Any],
    value: list[Any],
    *,
    path: str,
    errors: list[ToolError],
    depth: int,
) -> None:
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        errors.append(_invalid(f"{path} 至少需要 {min_items} 项", path=path))
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and len(value) > max_items:
        errors.append(_invalid(f"{path} 最多允许 {max_items} 项", path=path))
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            _validate_node(
                items, item, path=f"{path}[{index}]", errors=errors, depth=depth + 1
            )


def _validate_object(
    schema: dict[str, Any],
    value: dict[str, Any],
    *,
    path: str,
    errors: list[ToolError],
    depth: int,
) -> None:
    properties = schema.get("properties")
    schema_props = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                errors.append(_invalid(f"缺少必填参数 {key}", path=f"{path}.{key}"))
    additional = schema.get("additionalProperties")
    if additional is False:
        for key in value:
            if key not in schema_props:
                errors.append(_invalid(f"不支持的参数 {key}", path=f"{path}.{key}"))
    for key, sub_schema in schema_props.items():
        if key in value:
            _validate_node(
                sub_schema,
                value[key],
                path=f"{path}.{key}",
                errors=errors,
                depth=depth + 1,
            )


def _matches_type(expected: Any, value: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(item, value) for item in expected)
    mapping: dict[str, Any] = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "null": lambda v: v is None,
    }
    check = mapping.get(expected)
    if check is None:
        return True
    return bool(check(value))


def _json_type(value: Any) -> str:
    return {
        type(None): "null",
        bool: "boolean",
        int: "integer",
        float: "number",
        str: "string",
        list: "array",
        dict: "object",
    }.get(type(value), type(value).__name__)


def _invalid(message: str, *, path: str) -> ToolError:
    return ToolError(
        code="tool.invalid_args",
        message=message,
        hint=f"请按工具 schema 修正 {path} 后重试",
    )


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"字段 {key} 必须是非空字符串")
    return value


def _opt_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"字段 {key} 必须是字符串")
    return value


def _tuple_of_str(value: Any, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"字段 {key} 必须是字符串数组")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"字段 {key} 必须是字符串数组")
        result.append(item)
    return tuple(result)
