"""schema 校验:宁可拒绝,也不能把写错的用例跑成通过。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import simple_case, write_dataset

from evalkit.errors import SchemaError
from evalkit.schema import (
    load_cases,
    load_dataset,
    load_manifest,
    parse_case,
    parse_manifest,
    validate_grader,
)


def test_load_dataset_ok(tmp_path: Path) -> None:
    dataset = load_dataset(write_dataset(tmp_path, cases=[simple_case()]))
    assert dataset.manifest.name == "demo"
    assert dataset.manifest.version == 1
    assert dataset.case_ids() == ("demo/basic",)


def test_load_dataset_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="数据集目录不存在"):
        load_dataset(tmp_path / "missing")


def test_load_dataset_missing_files(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    with pytest.raises(SchemaError, match="manifest.json"):
        load_dataset(dataset)
    (dataset / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SchemaError, match="cases.jsonl"):
        load_dataset(dataset)


def test_load_dataset_empty_cases(tmp_path: Path) -> None:
    dataset = write_dataset(tmp_path, cases=[])
    with pytest.raises(SchemaError, match="没有用例"):
        load_dataset(dataset)


def test_load_dataset_duplicate_ids(tmp_path: Path) -> None:
    dataset = write_dataset(tmp_path, cases=[simple_case(), simple_case()])
    with pytest.raises(SchemaError, match="id 重复"):
        load_dataset(dataset)


def test_load_manifest_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(SchemaError, match="不是合法 JSON"):
        load_manifest(path)


def test_parse_manifest_validation() -> None:
    with pytest.raises(SchemaError, match="必须是 JSON 对象"):
        parse_manifest([])
    with pytest.raises(SchemaError, match="未知字段"):
        parse_manifest({"name": "x", "adapter": "a", "extra": 1})
    with pytest.raises(SchemaError, match="name 格式"):
        parse_manifest({"name": "Bad Name", "adapter": "a"})
    with pytest.raises(SchemaError, match="adapter"):
        parse_manifest({"name": "x", "adapter": ""})
    with pytest.raises(SchemaError, match="version"):
        parse_manifest({"name": "x", "adapter": "a", "version": 0})
    with pytest.raises(SchemaError, match="version"):
        parse_manifest({"name": "x", "adapter": "a", "version": True})
    with pytest.raises(SchemaError, match="description"):
        parse_manifest({"name": "x", "adapter": "a", "description": 1})
    with pytest.raises(SchemaError, match="owner"):
        parse_manifest({"name": "x", "adapter": "a", "owner": 1})
    with pytest.raises(SchemaError, match="tags"):
        parse_manifest({"name": "x", "adapter": "a", "tags": "x"})


def test_parse_manifest_defaults() -> None:
    manifest = parse_manifest({"name": "x-y", "adapter": "a", "tags": ["t"]})
    assert manifest.version == 1
    assert manifest.owner == "unknown"
    assert manifest.tags == ("t",)


def test_load_cases_skips_blank_and_reports_line(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text("\n" + json.dumps(simple_case()) + "\n\n", encoding="utf-8")
    assert len(load_cases(path)) == 1
    path.write_text("{\n", encoding="utf-8")
    with pytest.raises(SchemaError, match=":1:不是合法 JSON"):
        load_cases(path)


def test_parse_case_validation(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="必须是 JSON 对象"):
        parse_case("x")
    with pytest.raises(SchemaError, match="未知字段"):
        parse_case({**simple_case(), "extra": 1})
    with pytest.raises(SchemaError, match="id 格式"):
        parse_case({**simple_case(), "id": "Bad"})
    with pytest.raises(SchemaError, match="group 格式"):
        parse_case({**simple_case(), "group": "Bad Group"})
    with pytest.raises(SchemaError, match="input 必须是对象"):
        parse_case({**simple_case(), "input": []})
    with pytest.raises(SchemaError, match="graders 必须是非空列表"):
        parse_case({**simple_case(), "graders": []})
    with pytest.raises(SchemaError, match="tags"):
        parse_case({**simple_case(), "tags": [1]})
    with pytest.raises(SchemaError, match="notes"):
        parse_case({**simple_case(), "notes": 1})
    with pytest.raises(SchemaError, match="grader id 重复"):
        parse_case(
            {
                **simple_case(),
                "graders": [
                    {"type": "equals", "value": "x", "id": "same"},
                    {"type": "equals", "value": "x", "id": "same"},
                ],
            }
        )


@pytest.mark.parametrize(
    "spec, message",
    [
        ({"type": "nope"}, "未知类型"),
        ({"type": "equals", "value": 1, "extra": 1}, "未知字段"),
        ({"type": "equals", "value": 1, "id": ""}, "id 必须"),
        ({"type": "equals", "value": 1, "required": 1}, "required"),
        ({"type": "equals", "value": 1, "weight": 0}, "weight"),
        ({"type": "equals", "value": 1, "weight": True}, "weight"),
        ({"type": "equals", "value": 1, "message": 1}, "message"),
        ({"type": "equals", "value": 1, "field": ""}, "field"),
        ({"type": "equals"}, "缺少 value"),
        ({"type": "contains", "value": 1}, "value 必须是字符串"),
        ({"type": "regex", "pattern": ""}, "regex"),
        ({"type": "regex", "pattern": "["}, "regex 不合法"),
        ({"type": "includes", "values": [1]}, "缺少 field"),
        ({"type": "includes", "field": "f", "values": "x"}, "values"),
        ({"type": "count", "field": "f"}, "至少需要"),
        ({"type": "count", "field": "f", "min": 2, "max": 1}, "min 不能大于"),
        ({"type": "number", "field": "f", "min": True}, "min 必须是数字"),
        ({"type": "tool_calls", "values": [1]}, "values 的每项"),
        ({"type": "all", "graders": []}, "必须是非空列表"),
        ({"type": "not"}, "必须是对象"),
    ],
)
def test_validate_grader_rejects(spec: dict, message: str) -> None:
    with pytest.raises(SchemaError, match=message):
        validate_grader(spec)


def test_validate_grader_normalizes_all_types() -> None:
    specs = [
        {"type": "equals", "value": 1},
        {"type": "contains", "value": "x"},
        {"type": "not_contains", "value": "x"},
        {"type": "regex", "pattern": "x"},
        {"type": "includes", "field": "f", "values": [1, "a"]},
        {"type": "excludes", "field": "f", "values": [1]},
        {"type": "count", "field": "f", "min": 1},
        {"type": "number", "field": "f", "max": 2},
        {"type": "number", "field": "f", "equals": 2},
        {"type": "json_subset", "value": {"a": 1}},
        {"type": "tool_calls", "values": ["t"]},
        {
            "type": "all",
            "graders": [{"type": "contains", "value": "x"}],
        },
        {
            "type": "any",
            "graders": [{"type": "contains", "value": "x"}],
        },
        {
            "type": "not",
            "grader": {"type": "contains", "value": "x"},
        },
    ]
    for index, spec in enumerate(specs):
        normalized = validate_grader(spec, index=index)
        assert normalized["id"] == f"g{index + 1}"
        assert normalized["required"] is True
        assert normalized["weight"] == 1.0
    assert validate_grader({"type": "equals", "value": 1})["field"] == "output"


def test_load_cases_os_error(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="读取用例失败"):
        load_cases(tmp_path / "nope.jsonl")
