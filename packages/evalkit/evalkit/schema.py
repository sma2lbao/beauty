"""数据集 schema:manifest.json + cases.jsonl,加载即校验。

用例写错却跑出"通过"是最坏的评测失败,因此这里对未知字段、未知 grader
类型、字段类型和 id 格式都做显式校验,错误消息带文件与行号。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import SchemaError

#: 用例/套件 id 只允许小写字母数字与 . _ / -,防止报告、URL、文件名互相踩。
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
GROUP_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

#: 平台内置的确定性 grader 类型(新增类型时同步 graders.py 与文档)。
GRADER_TYPES = frozenset(
    {
        "equals",
        "contains",
        "not_contains",
        "regex",
        "includes",
        "excludes",
        "count",
        "number",
        "json_subset",
        "tool_calls",
        "all",
        "any",
        "not",
    }
)

_COMMON_GRADER_KEYS = frozenset({"type", "id", "required", "weight", "message"})
_GRADER_KEYS: dict[str, frozenset[str]] = {
    "equals": frozenset({"field", "value"}),
    "contains": frozenset({"field", "value"}),
    "not_contains": frozenset({"field", "value"}),
    "regex": frozenset({"field", "pattern"}),
    "includes": frozenset({"field", "values"}),
    "excludes": frozenset({"field", "values"}),
    "count": frozenset({"field", "min", "max"}),
    "number": frozenset({"field", "min", "max", "equals"}),
    "json_subset": frozenset({"field", "value"}),
    "tool_calls": frozenset({"values"}),
    "all": frozenset({"graders"}),
    "any": frozenset({"graders"}),
    "not": frozenset({"grader"}),
}
_DEFAULT_OUTPUT_GRADERS = frozenset(
    {"equals", "contains", "not_contains", "regex", "json_subset"}
)

_MANIFEST_KEYS = frozenset(
    {"name", "version", "description", "owner", "adapter", "tags"}
)
_CASE_KEYS = frozenset({"id", "group", "input", "graders", "tags", "notes"})


@dataclass(frozen=True)
class DatasetManifest:
    """一个数据集的身份与默认 adapter。"""

    name: str
    adapter: str
    version: int = 1
    description: str = ""
    owner: str = "unknown"
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    """一条评测用例:输入 + grader 列表,不含任何执行代码。"""

    id: str
    input: Mapping[str, Any]
    graders: tuple[Mapping[str, Any], ...]
    group: str = "default"
    tags: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class Dataset:
    """加载完成的用例集;cases 顺序即报告与基线比对顺序。"""

    manifest: DatasetManifest
    cases: tuple[Case, ...]
    path: Path = field(default_factory=Path)

    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.id for case in self.cases)


def load_dataset(path: str | Path) -> Dataset:
    """从目录加载 manifest.json 与 cases.jsonl,并完成全部校验。"""
    root = Path(path)
    if not root.is_dir():
        raise SchemaError(f"数据集目录不存在:{root}")
    manifest_path = root / "manifest.json"
    cases_path = root / "cases.jsonl"
    if not manifest_path.is_file():
        raise SchemaError(f"数据集缺少 manifest.json:{manifest_path}")
    if not cases_path.is_file():
        raise SchemaError(f"数据集缺少 cases.jsonl:{cases_path}")
    manifest = load_manifest(manifest_path)
    cases = load_cases(cases_path)
    if not cases:
        raise SchemaError(f"数据集没有用例:{cases_path}")
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise SchemaError(f"{cases_path}:用例 id 重复 {case.id}")
        seen.add(case.id)
    return Dataset(manifest=manifest, cases=cases, path=root)


def load_manifest(path: str | Path) -> DatasetManifest:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SchemaError(f"读取 manifest 失败:{manifest_path}:{exc}") from exc
    except json.JSONDecodeError as exc:
        raise SchemaError(f"manifest 不是合法 JSON:{manifest_path}:{exc}") from exc
    return parse_manifest(payload, where=str(manifest_path))


def parse_manifest(payload: Any, *, where: str = "manifest") -> DatasetManifest:
    if not isinstance(payload, Mapping):
        raise SchemaError(f"{where}:必须是 JSON 对象")
    unknown = sorted(set(payload) - _MANIFEST_KEYS)
    if unknown:
        raise SchemaError(f"{where}:未知字段 {unknown},支持 {sorted(_MANIFEST_KEYS)}")
    name = _require_str(payload.get("name"), f"{where}:name")
    if not GROUP_PATTERN.match(name):
        raise SchemaError(f"{where}:name 格式不合法 {name!r}")
    adapter = _require_str(payload.get("adapter"), f"{where}:adapter")
    version = payload.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SchemaError(f"{where}:version 必须是正整数")
    description = _optional_str(payload.get("description"), f"{where}:description")
    owner = _optional_str(payload.get("owner", "unknown"), f"{where}:owner")
    tags = _string_tuple(payload.get("tags", []), f"{where}:tags")
    return DatasetManifest(
        name=name,
        adapter=adapter,
        version=version,
        description=description,
        owner=owner,
        tags=tags,
    )


def load_cases(path: str | Path) -> tuple[Case, ...]:
    cases_path = Path(path)
    try:
        lines = cases_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SchemaError(f"读取用例失败:{cases_path}:{exc}") from exc
    cases: list[Case] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SchemaError(
                f"{cases_path}:{line_number}:不是合法 JSON:{exc}"
            ) from exc
        cases.append(parse_case(payload, where=f"{cases_path}:{line_number}"))
    return tuple(cases)


def parse_case(payload: Any, *, where: str = "用例") -> Case:
    if not isinstance(payload, Mapping):
        raise SchemaError(f"{where}:必须是 JSON 对象")
    unknown = sorted(set(payload) - _CASE_KEYS)
    if unknown:
        raise SchemaError(f"{where}:未知字段 {unknown},支持 {sorted(_CASE_KEYS)}")
    case_id = _require_str(payload.get("id"), f"{where}:id")
    if not ID_PATTERN.match(case_id):
        raise SchemaError(f"{where}:id 格式不合法 {case_id!r}")
    group = payload.get("group", "default")
    if not isinstance(group, str) or not GROUP_PATTERN.match(group):
        raise SchemaError(f"{where}:group 格式不合法 {group!r}")
    raw_input = payload.get("input")
    if not isinstance(raw_input, Mapping):
        raise SchemaError(f"{where}:input 必须是对象")
    raw_graders = payload.get("graders")
    if not _is_list(raw_graders) or not raw_graders:
        raise SchemaError(f"{where}:graders 必须是非空列表")
    graders = tuple(
        validate_grader(spec, where=f"{where} 的 grader", index=index)
        for index, spec in enumerate(raw_graders)
    )
    ids = [str(grader["id"]) for grader in graders]
    if len(ids) != len(set(ids)):
        raise SchemaError(f"{where}:grader id 重复")
    tags = _string_tuple(payload.get("tags", []), f"{where}:tags")
    notes = _optional_str(payload.get("notes"), f"{where}:notes")
    return Case(
        id=case_id,
        input=dict(raw_input),
        graders=graders,
        group=group,
        tags=tags,
        notes=notes,
    )


def validate_grader(
    spec: Any, *, where: str = "grader", index: int = 0
) -> dict[str, Any]:
    """校验并规范化一条 grader:补默认 field/id/required/weight。"""
    if not isinstance(spec, Mapping):
        raise SchemaError(f"{where}:必须是对象")
    normalized = dict(spec)
    grader_type = normalized.get("type")
    if grader_type not in GRADER_TYPES:
        raise SchemaError(
            f"{where}:未知类型 {grader_type!r},支持 {sorted(GRADER_TYPES)}"
        )
    allowed = _COMMON_GRADER_KEYS | _GRADER_KEYS[str(grader_type)]
    unknown = sorted(set(normalized) - allowed)
    if unknown:
        raise SchemaError(f"{where}:未知字段 {unknown},支持 {sorted(allowed)}")

    grader_id = normalized.setdefault("id", f"g{index + 1}")
    if not isinstance(grader_id, str) or not grader_id.strip():
        raise SchemaError(f"{where}:id 必须是非空字符串")
    required = normalized.setdefault("required", True)
    if not isinstance(required, bool):
        raise SchemaError(f"{where}:required 必须是布尔值")
    weight = normalized.setdefault("weight", 1.0)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
        raise SchemaError(f"{where}:weight 必须是正数")
    if "message" in normalized and not isinstance(normalized["message"], str):
        raise SchemaError(f"{where}:message 必须是字符串")

    if grader_type in _DEFAULT_OUTPUT_GRADERS:
        normalized.setdefault("field", "output")
    if "field" in normalized and (
        not isinstance(normalized["field"], str) or not normalized["field"]
    ):
        raise SchemaError(f"{where}:field 必须是非空字符串")

    if grader_type in {"equals", "json_subset"}:
        if "value" not in normalized:
            raise SchemaError(f"{where}:{grader_type} 缺少 value")
    elif grader_type in {"contains", "not_contains"}:
        if not isinstance(normalized.get("value"), str):
            raise SchemaError(f"{where}:{grader_type} 的 value 必须是字符串")
    elif grader_type == "regex":
        pattern = normalized.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise SchemaError(f"{where}:regex 缺少非空 pattern")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SchemaError(f"{where}:regex 不合法:{exc}") from exc
    elif grader_type in {"includes", "excludes"}:
        if "field" not in normalized:
            raise SchemaError(f"{where}:{grader_type} 缺少 field")
        normalized["values"] = list(
            _scalar_list(normalized.get("values"), f"{where}:values")
        )
    elif grader_type == "count":
        if "field" not in normalized:
            raise SchemaError(f"{where}:count 缺少 field")
        _validate_bounds(normalized, where, allow_equals=False)
    elif grader_type == "number":
        if "field" not in normalized:
            raise SchemaError(f"{where}:number 缺少 field")
        _validate_bounds(normalized, where, allow_equals=True)
    elif grader_type == "tool_calls":
        normalized["values"] = list(
            _string_list(normalized.get("values"), f"{where}:values")
        )
    elif grader_type in {"all", "any"}:
        raw = normalized.get("graders")
        if not _is_list(raw) or not raw:
            raise SchemaError(f"{where}:{grader_type}.graders 必须是非空列表")
        normalized["graders"] = [
            validate_grader(item, where=f"{where} 的 {grader_type}", index=position)
            for position, item in enumerate(raw)
        ]
    elif grader_type == "not":
        normalized["grader"] = validate_grader(
            normalized.get("grader"), where=f"{where} 的 not", index=0
        )
    return normalized


def _validate_bounds(
    spec: Mapping[str, Any], where: str, *, allow_equals: bool
) -> None:
    keys = ["min", "max"] + (["equals"] if allow_equals else [])
    present = [key for key in keys if key in spec]
    if not present:
        raise SchemaError(f"{where}:至少需要 {'/'.join(keys)} 之一")
    values: dict[str, float] = {}
    for key in present:
        value = spec[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError(f"{where}:{key} 必须是数字")
        values[key] = float(value)
    if "min" in values and "max" in values and values["min"] > values["max"]:
        raise SchemaError(f"{where}:min 不能大于 max")


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{where} 必须是非空字符串")
    return value.strip()


def _optional_str(value: Any, where: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SchemaError(f"{where} 必须是字符串")
    return value


def _string_tuple(value: Any, where: str) -> tuple[str, ...]:
    return tuple(_string_list(value, where))


def _string_list(value: Any, where: str) -> list[str]:
    if not _is_list(value):
        raise SchemaError(f"{where} 必须是列表")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise SchemaError(f"{where} 的每项必须是非空字符串")
        result.append(item)
    return result


def _scalar_list(value: Any, where: str) -> list[Any]:
    if not _is_list(value):
        raise SchemaError(f"{where} 必须是列表")
    result: list[Any] = []
    for item in value:
        if isinstance(item, (Mapping, list)):
            raise SchemaError(f"{where} 的每项必须是标量")
        result.append(item)
    return result


def _is_list(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
