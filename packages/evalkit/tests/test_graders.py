"""grader 行为:确定性、可解释、权重与嵌套语义。"""

from __future__ import annotations

from evalkit.graders import Observation, grade_case
from evalkit.schema import Case, validate_grader


def make_case(*specs: dict) -> Case:
    graders = tuple(
        validate_grader(spec, index=index) for index, spec in enumerate(specs)
    )
    return Case(id="case/one", input={}, graders=graders)


def outcome(case: Case, observation: Observation, index: int = 0):
    return grade_case(case, observation).outcomes[index]


def test_equals_and_numeric_equality() -> None:
    case = make_case({"type": "equals", "value": 1})
    assert outcome(case, Observation(output="", fields={"x": 1.0})).ok is False
    assert outcome(case, Observation(output=1)).ok is True
    assert outcome(case, Observation(output=True)).ok is False
    assert outcome(case, Observation()).ok is False


def test_equals_field_resolution() -> None:
    case = make_case({"type": "equals", "field": "fields.a.b", "value": 2})
    assert outcome(case, Observation(fields={"a": {"b": 2}})).ok is True
    assert outcome(case, Observation(fields={"a": {}})).ok is False


def test_contains_and_not_contains() -> None:
    case = make_case(
        {"type": "contains", "value": "abc"},
        {"type": "not_contains", "value": "xyz"},
    )
    grade = grade_case(case, Observation(output="abc"))
    assert grade.ok is True
    assert outcome(case, Observation(output="abc")).ok is True
    assert outcome(case, Observation(output="abc"), 1).ok is True
    assert outcome(case, Observation(output="xyz")).ok is False
    assert outcome(case, Observation(output=1)).ok is False


def test_regex() -> None:
    case = make_case({"type": "regex", "pattern": r"^周报：\d+$"})
    assert outcome(case, Observation(output="周报：12")).ok is True
    assert outcome(case, Observation(output="周报")).ok is False
    assert outcome(case, Observation(output=1)).ok is False


def test_includes_and_excludes() -> None:
    case = make_case(
        {"type": "includes", "field": "events", "values": ["a", 1]},
        {"type": "excludes", "field": "events", "values": ["b"]},
    )
    assert grade_case(case, Observation(events=("a", 1))).ok is True
    assert outcome(case, Observation(events=("a",))).ok is False
    assert outcome(case, Observation(events=("a", 1, "b")), 1).ok is False
    assert outcome(case, Observation(fields={})).ok is False


def test_membership_field_not_list() -> None:
    case = make_case({"type": "includes", "field": "fields.value", "values": ["a"]})
    result = outcome(case, Observation(fields={"value": "a"}))
    assert result.ok is False
    assert "不是列表" in result.message


def test_count() -> None:
    case = make_case(
        {"type": "count", "field": "events", "min": 2, "max": 3},
    )
    assert outcome(case, Observation(events=("a", "b"))).ok is True
    assert "小于下限" in outcome(case, Observation(events=("a",))).message
    assert "大于上限" in outcome(case, Observation(events=("a", "b", "c", "d"))).message
    assert "小于下限" in outcome(case, Observation()).message
    bad = make_case({"type": "count", "field": "fields.events", "min": 2})
    assert "没有长度" in outcome(bad, Observation(fields={"events": 1})).message


def test_count_string_mapping_set() -> None:
    case = make_case({"type": "count", "field": "output", "min": 1})
    assert outcome(case, Observation(output="abc")).ok is True
    case = make_case({"type": "count", "field": "fields.x", "min": 2})
    assert outcome(case, Observation(fields={"x": {"a": 1, "b": 2}})).ok is True
    assert outcome(case, Observation(fields={"x": {1, 2}})).ok is True


def test_number() -> None:
    case = make_case(
        {"type": "number", "field": "tokens", "min": 1, "max": 10},
    )
    assert outcome(case, Observation(fields={"tokens": 5})).ok is True
    assert "期望 >=" in outcome(case, Observation(fields={"tokens": 0})).message
    assert "期望 <=" in outcome(case, Observation(fields={"tokens": 11})).message
    assert "不是数字" in outcome(case, Observation(fields={"tokens": "5"})).message
    assert "不是数字" in outcome(case, Observation(fields={"tokens": True})).message
    assert "不存在" in outcome(case, Observation()).message
    case = make_case({"type": "number", "field": "x", "equals": 3})
    assert outcome(case, Observation(fields={"x": 3.0})).ok is True
    assert "期望 3" in outcome(case, Observation(fields={"x": 4})).message


def test_json_subset() -> None:
    case = make_case(
        {
            "type": "json_subset",
            "field": "fields.payload",
            "value": {"a": 1, "nested": {"b": [1, 2]}},
        }
    )
    observation = Observation(
        fields={"payload": {"a": 1, "nested": {"b": [2, 1, 3]}, "extra": True}}
    )
    assert outcome(case, observation).ok is True
    assert (
        outcome(
            case, Observation(fields={"payload": {"a": 2, "nested": {"b": [1, 2]}}})
        ).ok
        is False
    )
    assert outcome(case, Observation(fields={"payload": 1})).ok is False
    assert outcome(case, Observation(fields={"payload": "not json"})).ok is False
    assert outcome(case, Observation(fields={"payload": '{"a": 1}'})).ok is False
    assert "不存在" in outcome(case, Observation()).message


def test_tool_calls() -> None:
    case = make_case({"type": "tool_calls", "values": ["search", "write"]})
    assert (
        outcome(case, Observation(tool_calls=("search", "write", "other"))).ok is True
    )
    result = outcome(case, Observation(tool_calls=("search",)))
    assert result.ok is False
    assert "缺少" in result.message


def test_all_any_not() -> None:
    case = make_case(
        {
            "type": "all",
            "graders": [
                {"type": "contains", "value": "a"},
                {"type": "contains", "value": "b"},
            ],
        },
        {
            "type": "any",
            "graders": [
                {"type": "contains", "value": "x"},
                {"type": "contains", "value": "a"},
            ],
        },
        {
            "type": "not",
            "grader": {"type": "contains", "value": "z"},
        },
    )
    grade = grade_case(case, Observation(output="ab"))
    assert grade.ok is True
    assert grade.score == 1.0
    assert outcome(case, Observation(output="a")).ok is False
    assert outcome(case, Observation(output="a"), 1).ok is True
    assert outcome(case, Observation(output="ab"), 2).ok is True
    failed = outcome(case, Observation(output="z"))
    assert failed.ok is False
    assert "所有子条件都必须通过" in failed.message


def test_required_weight_and_custom_message() -> None:
    case = make_case(
        {
            "type": "equals",
            "value": "ok",
            "required": False,
            "message": "自定义失败",
        },
        {"type": "contains", "value": "x", "weight": 3},
    )
    grade = grade_case(case, Observation(output="x"))
    assert grade.ok is True
    assert grade.score == 0.75
    assert grade.failures == ()
    assert outcome(case, Observation(output="bad")).message == "自定义失败"
    assert outcome(case, Observation(output="bad"), 1).message


def test_execution_error_short_circuits() -> None:
    case = make_case({"type": "equals", "value": ""})
    grade = grade_case(case, Observation(error="boom"))
    assert grade.ok is False
    assert grade.score == 0.0
    assert grade.failures == ("执行错误:boom",)


def test_missing_output_and_long_message() -> None:
    case = make_case({"type": "contains", "value": "x"})
    result = outcome(case, Observation(output="y" * 300))
    assert result.ok is False
    assert "..." in result.message
    assert outcome(case, Observation()).ok is False


def test_outcome_to_dict_and_empty_weight() -> None:
    case = make_case({"type": "equals", "value": "x"})
    result = outcome(case, Observation(output="x"))
    assert result.to_dict()["ok"] is True
    empty = Case(id="empty", input={}, graders=())
    grade = grade_case(empty, Observation())
    assert grade.ok is True
    assert grade.score == 0.0
