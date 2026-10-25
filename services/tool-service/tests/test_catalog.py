"""目录契约:内置工具启用,别名机制保留但目录不再自带旧名。"""

from __future__ import annotations

import pytest
from common.tools import ExecutorKind, ToolDescriptor, ToolKind, ToolRisk

from tool_service.services import catalog


def test_catalog_version_is_exposed() -> None:
    assert catalog.catalog_version() == catalog.CATALOG_VERSION == "2026-10-25.p4"


def test_enabled_tools_are_the_function_slice() -> None:
    names = [tool.name for tool in catalog.descriptors()]
    assert names == [
        "read",
        "glob",
        "grep",
        "list_dir",
        "write",
        "edit",
        "search_knowledge",
        "fetch",
        "ask_question",
    ]
    risk_by_name = {tool.name: tool.risk for tool in catalog.descriptors()}
    assert risk_by_name["write"] is ToolRisk.WRITE
    assert risk_by_name["edit"] is ToolRisk.WRITE
    assert risk_by_name["search_knowledge"] is ToolRisk.READ
    assert risk_by_name["fetch"] is ToolRisk.READ
    assert risk_by_name["ask_question"] is ToolRisk.READ
    executor_by_name = {tool.name: tool.executor for tool in catalog.descriptors()}
    for sandbox_name in ("read", "glob", "grep", "list_dir", "write", "edit"):
        assert executor_by_name[sandbox_name] is ExecutorKind.SANDBOX
    assert executor_by_name["search_knowledge"] is ExecutorKind.HTTP
    assert executor_by_name["fetch"] is ExecutorKind.HTTP
    assert executor_by_name["ask_question"] is ExecutorKind.NONE


def test_interaction_tool_schema_is_decoupled() -> None:
    everything = [tool.name for tool in catalog.descriptors(include_disabled=True)]
    assert everything == [
        "read",
        "glob",
        "grep",
        "list_dir",
        "write",
        "edit",
        "search_knowledge",
        "fetch",
        "ask_question",
    ]
    assert catalog.find_descriptor("write").enabled is True
    assert catalog.find_descriptor("edit").risk is ToolRisk.WRITE
    ask = catalog.find_descriptor("ask_question")
    assert ask.enabled is True
    assert ask.kind is ToolKind.INTERACTION
    assert ask.executor is ExecutorKind.NONE
    properties = ask.input_schema["properties"]
    # allow_custom 是顶层公共开关,不在单题内部(设计 §7.7)。
    assert properties["allow_custom"]["type"] == "boolean"
    assert properties["allow_custom"]["default"] is True
    item = properties["questions"]["items"]
    assert "allow_custom" not in item["properties"]
    assert item["properties"]["multi_select"] == {"type": "boolean", "default": False}
    assert item["required"] == ["question", "options"]
    assert ask.input_schema["required"] == ["questions"]
    assert ask.input_schema["additionalProperties"] is False


def test_search_knowledge_descriptor_shape() -> None:
    search = catalog.find_descriptor("search_knowledge")
    assert search is not None
    assert search.risk is ToolRisk.READ
    assert search.kind is ToolKind.FUNCTION
    assert search.executor is ExecutorKind.HTTP
    assert search.timeout_seconds == 20.0
    schema = search.input_schema
    assert schema["required"] == ["query"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["query"]["maxLength"] == 2000
    assert schema["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 10,
        "default": 5,
    }


def test_fetch_descriptor_shape() -> None:
    fetch = catalog.find_descriptor("fetch")
    assert fetch is not None
    assert fetch.risk is ToolRisk.READ
    assert fetch.kind is ToolKind.FUNCTION
    assert fetch.executor is ExecutorKind.HTTP
    assert fetch.timeout_seconds == 20.0
    schema = fetch.input_schema
    assert schema["required"] == ["url"]
    assert schema["additionalProperties"] is False
    url = schema["properties"]["url"]
    assert url["type"] == "string"
    assert url["pattern"] == "^https?://"
    assert url["maxLength"] == 2048


def test_alias_mechanism_kept_but_catalog_has_no_legacy_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 旧 code_* 别名(P2 末下线)不再随目录发布;通用解析机制保留给后续兼容映射。
    assert catalog.ALIASES == {}
    assert catalog.resolve_name("not_a_tool") == "not_a_tool"
    assert catalog.find_descriptor("not_a_tool") is None

    monkeypatch.setitem(catalog.ALIASES, "legacy_local_read", "read")
    assert catalog.resolve_name("legacy_local_read") == "read"
    descriptor = catalog.find_descriptor("legacy_local_read")
    assert descriptor is not None and descriptor.name == "read"


def test_openai_tools_shape() -> None:
    tools = catalog.openai_tools()
    assert len(tools) == 9
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
        "search_knowledge",
        "fetch",
        "ask_question",
    ]


def test_dumps_schema_is_deterministic() -> None:
    first = catalog.dumps_schema(catalog.READ)
    assert first == catalog.dumps_schema(catalog.READ)
    assert '"path"' in first
