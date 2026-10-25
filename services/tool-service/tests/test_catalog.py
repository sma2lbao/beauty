"""目录契约:P0 只读竖切 + 写/交互工具预埋 + 别名解析。"""

from __future__ import annotations

from common.tools import ExecutorKind, ToolDescriptor, ToolKind, ToolRisk

from tool_service.services import catalog


def test_catalog_version_is_exposed() -> None:
    assert catalog.catalog_version() == catalog.CATALOG_VERSION


def test_enabled_tools_are_the_readonly_slice() -> None:
    names = [tool.name for tool in catalog.descriptors()]
    assert names == ["read", "glob", "grep", "list_dir"]
    assert all(tool.risk is ToolRisk.READ for tool in catalog.descriptors())
    assert all(tool.executor is ExecutorKind.SANDBOX for tool in catalog.descriptors())


def test_write_and_interaction_tools_are_predeclared_disabled() -> None:
    everything = [tool.name for tool in catalog.descriptors(include_disabled=True)]
    assert everything == [
        "read",
        "glob",
        "grep",
        "list_dir",
        "write",
        "edit",
        "ask_question",
    ]
    assert catalog.find_descriptor("write").enabled is False
    assert catalog.find_descriptor("edit").risk is ToolRisk.WRITE
    ask = catalog.find_descriptor("ask_question")
    assert ask.kind is ToolKind.INTERACTION
    assert ask.executor is ExecutorKind.NONE


def test_alias_resolution() -> None:
    assert catalog.resolve_name("code_read_file") == "read"
    assert catalog.find_descriptor("code_list_files").name == "glob"
    assert catalog.resolve_name("not_a_tool") == "not_a_tool"
    assert catalog.find_descriptor("not_a_tool") is None


def test_openai_tools_shape() -> None:
    tools = catalog.openai_tools()
    assert len(tools) == 4
    for item in tools:
        assert item["type"] == "function"
        function = item["function"]
        assert set(function) == {"name", "description", "parameters"}
        assert function["parameters"]["type"] == "object"
        assert function["parameters"]["additionalProperties"] is False


def test_descriptors_payload_roundtrip() -> None:
    payloads = catalog.descriptors_payload()
    rebuilt = [ToolDescriptor.from_payload(payload) for payload in payloads]
    assert [tool.name for tool in rebuilt] == ["read", "glob", "grep", "list_dir"]


def test_dumps_schema_is_deterministic() -> None:
    first = catalog.dumps_schema(catalog.READ)
    assert first == catalog.dumps_schema(catalog.READ)
    assert '"path"' in first
