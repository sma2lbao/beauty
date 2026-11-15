"""本地文件与执行类工具描述符:read/glob/grep/list_dir/write/edit/exec/run_code。"""

from __future__ import annotations

from common.tools import ExecutorKind, ToolDescriptor, ToolKind, ToolRisk

from tool_service.constants import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    DEFAULT_RUN_CODE_TIMEOUT_SECONDS,
    MAX_RUN_CODE_CHARS,
    MAX_RUN_CODE_TIMEOUT_SECONDS,
)

#: 目录版本随镜像发布,写进 /v1/tools 响应与审计行。
CATALOG_VERSION = "2026-11-10.p8"

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
        "把完整内容写入工作区文件(原子替换)。新建文件(目标不存在)可直接写、无需先 read;"
        "覆盖已有文件前必须先 read 该路径(读前置校验),"
        "也可用 expected_sha256 做乐观锁,overwrite=false 时拒绝覆盖。"
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
    enabled=True,
)

EXEC = ToolDescriptor(
    name="exec",
    description=(
        "在工作区沙箱内执行一条 shell 命令(/bin/sh -lc),返回退出码与 stdout/stderr。"
        "用于跑测试、构建、脚本等必须有进程的场景;读文件优先用 read 工具。"
        "命令在沙箱内执行且沙箱默认无网络;输出会被截断,"
        "超长输出请重定向到文件后用 read 分段读取。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4000,
                "description": "交给 /bin/sh -lc 的完整命令;可用管道、重定向与 &&。",
            },
            "cwd": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512,
                "description": "工作目录,相对路径按 /workspace 解析;默认 /workspace。",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 120,
                "description": "本次命令超时(秒),默认 60。",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    risk=ToolRisk.DESTRUCTIVE,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=DEFAULT_EXEC_TIMEOUT_SECONDS,
    enabled=True,
)

RUN_CODE = ToolDescriptor(
    name="run_code",
    description=(
        "在工作区沙箱内执行一段 Python 3 代码(经 stdin 直传,不经 shell),"
        "返回退出码与 stdout/stderr;用于数据处理、计算、生成文件等多行脚本,"
        "一次性 shell 命令请用 exec。代码以 cwd 为工作目录启动,沙箱默认无网络;"
        "输出会被截断,大结果请写入文件后用 read 分段读取。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_RUN_CODE_CHARS,
                "description": "完整 Python 3 源码;等价于从 stdin 传给 python3 -,"
                "顶层语句会直接执行,不会自动调用 main()。",
            },
            "cwd": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512,
                "description": "工作目录,相对路径按 /workspace 解析;默认 /workspace。",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_RUN_CODE_TIMEOUT_SECONDS,
                "default": 60,
                "description": "本次代码执行超时(秒),默认 60。",
            },
        },
        "required": ["code"],
        "additionalProperties": False,
    },
    risk=ToolRisk.DESTRUCTIVE,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    timeout_seconds=DEFAULT_RUN_CODE_TIMEOUT_SECONDS,
    enabled=True,
)
