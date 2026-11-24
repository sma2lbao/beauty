"""确定性 grader:把 Observation 与用例声明比对,不调用模型。

grader 是平台的核心契约:同一份用例在本地、CI、不同 adapter 上应得到
一致的通过/失败结论。所有失败消息用中文写明期望与实际,便于直接贴到报告。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from typing import Any

from .schema import Case

#: 字段不存在时用哨兵区分"值为 None"与"没这个字段"。
MISSING = object()


@dataclass(frozen=True)
class Observation:
    """adapter 返回的观测结果;grader 只读这个结构,不关心被测目标内部。"""

    output: str = ""
    fields: Mapping[str, Any] = field(default_factory=dict)
    events: tuple[str, ...] = ()
    tool_calls: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class GraderOutcome:
    """单条 grader 的判定结果。"""

    id: str
    type: str
    ok: bool
    required: bool
    weight: float
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "ok": self.ok,
            "required": self.required,
            "weight": self.weight,
            "message": self.message,
        }


@dataclass(frozen=True)
class CaseGrade:
    """一条用例的汇总评分。"""

    ok: bool
    score: float
    outcomes: tuple[GraderOutcome, ...]
    failures: tuple[str, ...]


def grade_case(case: Case, observation: Observation) -> CaseGrade:
    """按用例声明的 grader 顺序评分;执行错误直接判失败。"""
    if observation.error:
        return CaseGrade(
            ok=False,
            score=0.0,
            outcomes=(),
            failures=(f"执行错误:{observation.error}",),
        )
    outcomes = tuple(_evaluate(spec, observation) for spec in case.graders)
    failures = tuple(
        outcome.message for outcome in outcomes if outcome.required and not outcome.ok
    )
    total_weight = sum(outcome.weight for outcome in outcomes)
    passed_weight = sum(outcome.weight for outcome in outcomes if outcome.ok)
    score = passed_weight / total_weight if total_weight else 0.0
    return CaseGrade(
        ok=not failures,
        score=round(score, 4),
        outcomes=outcomes,
        failures=failures,
    )


def _evaluate(spec: Mapping[str, Any], observation: Observation) -> GraderOutcome:
    grader_type = str(spec["type"])
    field_name = str(spec.get("field", "output"))
    actual = _resolve(observation, field_name)

    if grader_type == "equals":
        ok = actual is not MISSING and _same(actual, spec.get("value"))
        return _outcome(
            spec,
            ok,
            _expected_message(field_name, spec.get("value"), actual),
        )
    if grader_type == "contains":
        ok = isinstance(actual, str) and str(spec["value"]) in actual
        return _outcome(
            spec,
            ok,
            _expected_message(
                field_name, f"包含 {spec['value']!r}", actual, type_hint=True
            ),
        )
    if grader_type == "not_contains":
        ok = isinstance(actual, str) and str(spec["value"]) not in actual
        return _outcome(
            spec,
            ok,
            _expected_message(
                field_name, f"不包含 {spec['value']!r}", actual, type_hint=True
            ),
        )
    if grader_type == "regex":
        ok = isinstance(actual, str) and bool(re.search(str(spec["pattern"]), actual))
        return _outcome(
            spec,
            ok,
            _expected_message(
                field_name, f"匹配正则 {spec['pattern']!r}", actual, type_hint=True
            ),
        )
    if grader_type in {"includes", "excludes"}:
        ok, message = _check_membership(
            grader_type, field_name, actual, list(spec["values"])
        )
        return _outcome(spec, ok, message)
    if grader_type == "count":
        ok, message = _check_count(spec, field_name, actual)
        return _outcome(spec, ok, message)
    if grader_type == "number":
        ok, message = _check_number(spec, field_name, actual)
        return _outcome(spec, ok, message)
    if grader_type == "json_subset":
        ok, message = _check_json_subset(field_name, actual, spec.get("value"))
        return _outcome(spec, ok, message)
    if grader_type == "tool_calls":
        ok, message = _check_membership(
            "includes", "tool_calls", observation.tool_calls, list(spec["values"])
        )
        return _outcome(spec, ok, message)
    if grader_type in {"all", "any"}:
        children = [_evaluate(child, observation) for child in spec["graders"]]
        if grader_type == "all":
            ok = all(child.ok for child in children)
            default = "所有子条件都必须通过"
        else:
            ok = any(child.ok for child in children)
            default = "至少一个子条件通过"
        details = "; ".join(child.message for child in children if not child.ok)
        return _outcome(spec, ok, f"{default}({details})" if details else default)
    if grader_type == "not":
        child = _evaluate(spec["grader"], observation)
        return _outcome(
            spec,
            not child.ok,
            f"期望子条件失败,实际通过:{child.message}" if child.ok else "",
        )
    return _outcome(spec, False, f"未知 grader 类型:{grader_type}")


def _outcome(spec: Mapping[str, Any], ok: bool, default_message: str) -> GraderOutcome:
    return GraderOutcome(
        id=str(spec.get("id", spec["type"])),
        type=str(spec["type"]),
        ok=ok,
        required=bool(spec.get("required", True)),
        weight=float(spec.get("weight", 1.0)),
        message="" if ok else str(spec.get("message") or default_message),
    )


def _resolve(observation: Observation, field_name: str) -> Any:
    if field_name in {"", "output"}:
        return observation.output
    if field_name == "events":
        return observation.events
    if field_name == "tool_calls":
        return observation.tool_calls
    if field_name.startswith("fields."):
        field_name = field_name[len("fields.") :]
    current: Any = observation.fields
    for part in field_name.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return MISSING
        current = current[part]
    return current


def _check_membership(
    kind: str, field_name: str, actual: Any, expected: list[Any]
) -> tuple[bool, str]:
    if actual is MISSING:
        return False, f"字段 {field_name} 不存在"
    if isinstance(actual, (str, bytes)) or not isinstance(actual, Sequence):
        return False, f"字段 {field_name} 不是列表"
    values = list(actual)
    if kind == "includes":
        missing = [item for item in expected if not _any_same(values, item)]
        return (
            not missing,
            f"字段 {field_name} 缺少 {missing!r},实际 {values!r}",
        )
    present = [item for item in expected if _any_same(values, item)]
    return (
        not present,
        f"字段 {field_name} 不应包含 {present!r},实际 {values!r}",
    )


def _check_count(
    spec: Mapping[str, Any], field_name: str, actual: Any
) -> tuple[bool, str]:
    if actual is MISSING:
        return False, f"字段 {field_name} 不存在"
    if not isinstance(actual, (str, bytes, Sequence, Set, Mapping)):
        return False, f"字段 {field_name} 没有长度"
    size = len(actual)
    minimum = spec.get("min")
    maximum = spec.get("max")
    if minimum is not None and size < int(minimum):
        return False, f"字段 {field_name} 长度 {size} 小于下限 {minimum}"
    if maximum is not None and size > int(maximum):
        return False, f"字段 {field_name} 长度 {size} 大于上限 {maximum}"
    return True, ""


def _check_number(
    spec: Mapping[str, Any], field_name: str, actual: Any
) -> tuple[bool, str]:
    if actual is MISSING:
        return False, f"字段 {field_name} 不存在"
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False, f"字段 {field_name} 不是数字:{actual!r}"
    if "equals" in spec and not _same(actual, spec["equals"]):
        return False, f"字段 {field_name} 期望 {spec['equals']!r},实际 {actual!r}"
    if "min" in spec and actual < spec["min"]:
        return False, f"字段 {field_name} 期望 >= {spec['min']},实际 {actual}"
    if "max" in spec and actual > spec["max"]:
        return False, f"字段 {field_name} 期望 <= {spec['max']},实际 {actual}"
    return True, ""


def _check_json_subset(field_name: str, actual: Any, expected: Any) -> tuple[bool, str]:
    if actual is MISSING:
        return False, f"字段 {field_name} 不存在"
    if isinstance(actual, str):
        try:
            actual = json.loads(actual)
        except json.JSONDecodeError:
            return False, f"字段 {field_name} 不是合法 JSON"
    if _is_subset(actual, expected):
        return True, ""
    return (
        False,
        f"字段 {field_name} 不满足 JSON 子集,期望 {expected!r},实际 {actual!r}",
    )


def _is_subset(actual: Any, expected: Any) -> bool:
    """对象按 key 递归子集;数组按元素存在性;标量严格相等。"""
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _is_subset(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_is_subset(item, expected_item) for item in actual)
            for expected_item in expected
        )
    return _same(actual, expected)


def _any_same(values: Sequence[Any], expected: Any) -> bool:
    return any(_same(value, expected) for value in values)


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return (
            isinstance(actual, bool)
            and isinstance(expected, bool)
            and actual == expected
        )
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    return actual == expected


def _expected_message(
    field_name: str,
    expected: Any,
    actual: Any,
    *,
    type_hint: bool = False,
) -> str:
    shown = "<不存在>" if actual is MISSING else _short(actual)
    message = f"字段 {field_name} 期望 {expected!r},实际 {shown}"
    if type_hint and actual is not MISSING and not isinstance(actual, str):
        message += f"(类型 {type(actual).__name__},需要字符串)"
    return message


def _short(value: Any, limit: int = 200) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "..."
