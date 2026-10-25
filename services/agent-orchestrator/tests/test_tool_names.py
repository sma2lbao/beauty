"""历史工具名兼容(tool_names)与快照读取边界的回归测试。

P2 之后 catalog 不再提供 code_* 别名(见 test_catalog 的版本断言),历史
Run.skill_tools 快照里的旧名由 normalize_tool_names 在读取边界归一。
本文件锁住三件事:映射表本身、归一去重保序语义、旧白名单经
RemoteToolkit.restricted() 仍能装配出对应新工具。
"""

from __future__ import annotations

from typing import Any

import httpx
from common.tools import ExecutorKind, ToolDescriptor, ToolKind, ToolRisk

from agent_orchestrator.models.runs import Run
from agent_orchestrator.services.run_execution import _allowed_tools_from_snapshot
from agent_orchestrator.services.tool_client import (
    RemoteToolkit,
    ToolCatalog,
    ToolClient,
)
from agent_orchestrator.services.tool_names import (
    LEGACY_TOOL_NAMES,
    normalize_tool_names,
)

BASE_URL = "http://tool.local"
CATALOG_TOOLS: tuple[str, ...] = ("read", "write", "edit", "glob", "grep")


def _descriptor(name: str) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f"{name} 工具",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        kind=ToolKind.FUNCTION,
        executor=ExecutorKind.NONE,
    )


def _catalog_payload() -> dict[str, Any]:
    return {
        "tools": [_descriptor(name).to_payload() for name in CATALOG_TOOLS],
        "catalog_version": "tool-names-test",
    }


async def _ready_toolkit() -> tuple[RemoteToolkit, ToolClient]:
    """构造已加载目录的 RemoteToolkit(零网络:httpx.MockTransport)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=_catalog_payload())

    client = ToolClient(BASE_URL, transport=httpx.MockTransport(handler))
    catalog = ToolCatalog()
    toolkit = RemoteToolkit("t-1", client=client, catalog=catalog)
    await toolkit.ensure_ready()
    return toolkit, client


# ------------------------------------------------------------------ 映射表


def test_legacy_map_covers_exactly_the_five_code_tools() -> None:
    assert LEGACY_TOOL_NAMES == {
        "code_read_file": "read",
        "code_write_file": "write",
        "code_edit_file": "edit",
        "code_search_text": "grep",
        "code_list_files": "glob",
    }


def test_normalize_maps_dedupes_and_keeps_order() -> None:
    names = ["code_list_files", "read", "glob", "code_read_file", "grep", "read"]

    assert normalize_tool_names(names) == ["glob", "read", "grep"]


def test_normalize_keeps_unknown_names_untouched() -> None:
    assert normalize_tool_names(["ask_question", "search_knowledge", "write"]) == [
        "ask_question",
        "search_knowledge",
        "write",
    ]


def test_normalize_none_and_empty() -> None:
    assert normalize_tool_names(None) == []
    assert normalize_tool_names([]) == []


# ----------------------------------------------------- 快照读取边界与图装配层


def test_snapshot_boundary_normalizes_legacy_names() -> None:
    run = Run(skill_tools=["code_read_file", "code_list_files", "read"])

    assert _allowed_tools_from_snapshot(run) == ["read", "glob"]


def test_snapshot_boundary_keeps_unrestricted_and_empty_semantics() -> None:
    assert _allowed_tools_from_snapshot(Run()) is None
    assert _allowed_tools_from_snapshot(Run(skill_tools=[])) == []


async def test_legacy_snapshot_assembles_new_tools() -> None:
    toolkit, client = await _ready_toolkit()
    try:
        allowed = _allowed_tools_from_snapshot(
            Run(skill_tools=["code_read_file", "code_list_files", "code_search_text"])
        )
        assert allowed is not None

        restricted = toolkit.restricted(allowed)

        assert restricted.names == ["read", "glob", "grep"]
    finally:
        await client.aclose()


async def test_unmapped_legacy_names_would_assemble_empty_toolkit() -> None:
    """反证:不做归一直接喂旧名,catalog 查不到,工具箱为空。"""

    toolkit, client = await _ready_toolkit()
    try:
        assert toolkit.restricted(["code_read_file"]).names == []
    finally:
        await client.aclose()
