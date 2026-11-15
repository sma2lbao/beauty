"""工具注册表:全量列表、名字/别名解析与对外序列化。"""

from __future__ import annotations

import json
from typing import Any

from common.tools import ToolDescriptor

from .file_tools import (
    CATALOG_VERSION,
    EDIT,
    EXEC,
    GLOB,
    GREP,
    LIST_DIR,
    READ,
    RUN_CODE,
    WRITE,
)
from .remote_tools import (
    ASK_QUESTION,
    FETCH,
    JOB_KILL,
    JOB_OUTPUT,
    JOB_START,
    SEARCH_KNOWLEDGE,
    WEB_SEARCH,
)

#: 全部工具:read、glob、grep、list_dir、write、edit、exec、run_code、
#: search_knowledge、fetch、web_search、job_start、job_output、job_kill、
#: ask_question。
TOOLS: tuple[ToolDescriptor, ...] = (
    READ,
    GLOB,
    GREP,
    LIST_DIR,
    WRITE,
    EDIT,
    EXEC,
    RUN_CODE,
    SEARCH_KNOWLEDGE,
    FETCH,
    WEB_SEARCH,
    JOB_START,
    JOB_OUTPUT,
    JOB_KILL,
    ASK_QUESTION,
)

_BY_NAME: dict[str, ToolDescriptor] = {tool.name: tool for tool in TOOLS}
ALIASES: dict[str, str] = {alias: tool.name for tool in TOOLS for alias in tool.aliases}


def catalog_version() -> str:
    """返回目录版本号(写进 /v1/tools 响应)。"""

    return CATALOG_VERSION


def resolve_name(name: str) -> str:
    """把别名解析成正式工具名;未知名称原样返回(由查目录判 404)。"""

    if name in _BY_NAME:
        return name
    return ALIASES.get(name, name)


def find_descriptor(name: str) -> ToolDescriptor | None:
    """按正式名或别名查目录;不区分 enabled(调用方自己判断)。"""

    return _BY_NAME.get(resolve_name(name))


def descriptors(*, include_disabled: bool = False) -> tuple[ToolDescriptor, ...]:
    """按注册顺序返回工具描述;默认只返回启用的。"""

    if include_disabled:
        return TOOLS
    return tuple(tool for tool in TOOLS if tool.enabled)


def descriptors_payload(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    """返回 wire 形状的工具列表(供 ToolClient 直接使用)。"""

    return [
        tool.to_payload() for tool in descriptors(include_disabled=include_disabled)
    ]


def openai_tools(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    """转成 OpenAI tools 形状,供模型调用;仅启用工具。"""

    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in descriptors(include_disabled=include_disabled)
    ]


def dumps_schema(tool: ToolDescriptor) -> str:
    """调试用:把工具 schema 序列化成紧凑 JSON。"""

    return json.dumps(tool.input_schema, ensure_ascii=False, sort_keys=True)
