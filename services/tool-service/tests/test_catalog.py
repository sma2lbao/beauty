"""目录契约:P1 受控写(read/glob/grep/list_dir/write/edit 启用,ask 预埋)。"""

from __future__ import annotations

from common.tools import ExecutorKind, ToolDescriptor, ToolKind, ToolRisk

from tool_service.services import catalog


def test_catalog_version_is_exposed() -> None:
    assert catalog.catalog_version() == catalog.CATALOG_VERSION


def test_enabled_tools_are_the_function_slice() -> None:
    names = [tool.name for tool in catalog.descriptors()]
    assert names == ["read", "glob", "grep", "list_dir", "write", "edit"]
    risk_by_name = {tool.name: tool.risk for tool in catalog.descriptors()}
    assert risk_by_name["write"] is ToolRisk.WRITE
    assert risk_by_name["edit"] is ToolRisk.WRITE
    assert all(tool.executor is ExecutorKind.SANDBOX for tool in catalog.descriptors())


def test_interaction_tool_is_predeclared_disabled() -> None:
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
    assert catalog.find_descriptor("write").enabled is True
    assert catalog.find_descriptor("edit").risk is ToolRisk.WRITE
    ask = catalog.find_descriptor("ask_question")
    assert ask.enabled is False
    assert ask.kind is ToolKind.INTERACTION
    assert ask.executor is ExecutorKind.NONE


def test_alias_resolution() -> None:
    assert catalog.resolve_name("code_read_file") == "read"
    assert catalog.find_descriptor("code_list_files").name == "glob"
    assert catalog.resolve_name("code_write_file") == "write"
    assert catalog.resolve_name("not_a_tool") == "not_a_tool"
    assert catalog.find_descriptor("not_a_tool") is None


def test_openai_tools_shape() -> None:
    tools = catalog.openai_tools()
    assert len(tools) == 6
    for item in tools:
        assert item["type"] == "function"
        function = item["function"]
        assert set(function) == {"name", "description", "parameters"}
        assert function["parameters"]["type"] == "object"
        assert function["parameters"]["additionalProperties"] is False


def test_descriptors_payload_roundtrip() -> None:
    payloads = catalog.descriptors_payload()
    rebuilt = [ToolDescriptor.from_payload(payload) for payload in payloads]
    assert [tool.name for tool in rebuilt] == [
        "read",
        "glob",
        "grep",
        "list_dir",
        "write",
        "edit",
    ]


def test_dumps_schema_is_deterministic() -> None:
    first = catalog.dumps_schema(catalog.READ)
    assert first == catalog.dumps_schema(catalog.READ)
    assert '"path"' in first
