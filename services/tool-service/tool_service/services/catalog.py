"""内置工具目录(P1:只读 + 受控写;交互工具预埋 enabled=False)。

目录真源在代码而不是数据库:工具的 schema 随镜像版本发布,业务库只存
调用审计与幂等记录。这样 catalog 与实现同版本、评审走代码评审、
回滚走镜像;租户级覆盖(P3)再引入 DB。
"""

from __future__ import annotations

import json
from typing import Any

from common.tools import (
    ExecutorKind,
    ToolDescriptor,
    ToolKind,
    ToolRisk,
)

#: 目录版本随镜像发布,写进 /v1/tools 响应与审计行。
CATALOG_VERSION = "2026-10-25.p1"

READ = ToolDescriptor(
    name="read",
    description=(
        "读取工作区内文本文件,返回带行号的内容与截断元数据。"
        "读大文件用 offset/limit 分段。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
            "offset": {"type": "integer", "minimum": 1, "maximum": 1000000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
            "max_line_chars": {"type": "integer", "minimum": 80, "maximum": 4000},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=20.0,
    aliases=("code_read_file",),
)

GLOB = ToolDescriptor(
    name="glob",
    description="按 glob 模式列出工作区内的文件路径。* 不跨目录,** 跨目录。",
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "minLength": 1, "maxLength": 512},
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
            "include_dirs": {"type": "boolean"},
            "case_sensitive": {"type": "boolean"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=20.0,
    aliases=("code_list_files",),
)

GREP = ToolDescriptor(
    name="grep",
    description=(
        "在文本文件内按正则表达式搜索,返回匹配行与行列号。"
        "pattern 是 Python 正则风味;跨行搜索加 multiline。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "minLength": 1, "maxLength": 1000},
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
            "glob": {"type": "string", "minLength": 1, "maxLength": 512},
            "case_sensitive": {"type": "boolean"},
            "fixed_strings": {"type": "boolean"},
            "multiline": {"type": "boolean"},
            "context_lines": {"type": "integer", "minimum": 0, "maximum": 3},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=30.0,
    aliases=("code_search_text",),
)

LIST_DIR = ToolDescriptor(
    name="list_dir",
    description="列出目录内容(按需递归 1-3 层),返回名称、类型、大小与修改时间。",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
            "depth": {"type": "integer", "minimum": 1, "maximum": 3},
            "include_hidden": {"type": "boolean"},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "required": [],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=20.0,
)

WRITE = ToolDescriptor(
    name="write",
    description=(
        "把完整内容写入工作区文件(原子替换)。写已有文件前通常需要先 read(读前置校验);"
        "可用 expected_sha256 做乐观锁,overwrite=false 时拒绝覆盖。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
            "content": {"type": "string"},
            "overwrite": {"type": "boolean"},
            "create_parents": {"type": "boolean"},
            "expected_sha256": {"type": "string", "maxLength": 64},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
    risk=ToolRisk.WRITE,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=30.0,
    aliases=("code_write_file",),
    enabled=True,
)

EDIT = ToolDescriptor(
    name="edit",
    description=(
        "在文件内做一次精确字符串替换;old_string 必须唯一匹配,"
        "多处替换需 replace_all=true。写前通常需要先 read 该文件。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 512},
            "old_string": {"type": "string", "minLength": 1},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"},
            "expected_sha256": {"type": "string", "maxLength": 64},
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    },
    risk=ToolRisk.WRITE,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=30.0,
    aliases=("code_edit_file",),
    enabled=True,
)

ASK_QUESTION = ToolDescriptor(
    name="ask_question",
    description=(
        "向用户提出 1-4 个结构化问题并等待回答。运行时挂起任务并在用户"
        "回答后恢复;工具本身不执行,由编排层处理中断。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1000,
                        },
                        "header": {"type": "string", "minLength": 1, "maxLength": 12},
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 80,
                                    },
                                    "description": {
                                        "type": "string",
                                        "maxLength": 300,
                                    },
                                },
                                "required": ["label"],
                                "additionalProperties": False,
                            },
                        },
                        "multi_select": {"type": "boolean"},
                        "allow_custom": {"type": "boolean"},
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.INTERACTION,
    executor=ExecutorKind.NONE,
    timeout_seconds=30.0,
    enabled=False,
)

#: 全部工具(P1:read/glob/grep/list_dir/write/edit 启用;ask_question 预埋)。
TOOLS: tuple[ToolDescriptor, ...] = (
    READ,
    GLOB,
    GREP,
    LIST_DIR,
    WRITE,
    EDIT,
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
