"""内置工具目录:只读 + 受控写 + 命令/代码执行 + 知识库与联网只读 + 交互工具。

包化拆分(W5 出口):file_tools/remote_tools/registry;
本 __init__ 是门面,保持 tool_service.services.catalog 的原有导入路径与名字不变。

"""

from __future__ import annotations

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
from .registry import (
    _BY_NAME,
    ALIASES,
    TOOLS,
    catalog_version,
    descriptors,
    descriptors_payload,
    dumps_schema,
    find_descriptor,
    openai_tools,
    resolve_name,
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

__all__ = [
    "CATALOG_VERSION",
    "READ",
    "GLOB",
    "GREP",
    "LIST_DIR",
    "WRITE",
    "EDIT",
    "EXEC",
    "RUN_CODE",
    "SEARCH_KNOWLEDGE",
    "FETCH",
    "WEB_SEARCH",
    "JOB_START",
    "JOB_OUTPUT",
    "JOB_KILL",
    "ASK_QUESTION",
    "TOOLS",
    "_BY_NAME",
    "ALIASES",
    "catalog_version",
    "resolve_name",
    "find_descriptor",
    "descriptors",
    "descriptors_payload",
    "openai_tools",
    "dumps_schema",
    "json",
    "Any",
    "ExecutorKind",
    "ToolDescriptor",
    "ToolKind",
    "ToolRisk",
    "DEFAULT_EXEC_TIMEOUT_SECONDS",
    "DEFAULT_RUN_CODE_TIMEOUT_SECONDS",
    "MAX_RUN_CODE_CHARS",
    "MAX_RUN_CODE_TIMEOUT_SECONDS",
]
