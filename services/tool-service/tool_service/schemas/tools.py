"""工具目录 / 执行 / 审计查询的 HTTP schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from common.tools import ToolDescriptor
from pydantic import BaseModel, ConfigDict, Field

TOOL_CALL_ID_MAX = 128
CONTEXT_ID_MAX = 32
SCOPE_KEY_MAX = 96
EXECUTE_TIMEOUT_MAX = 600
LIST_LIMIT_MAX = 200


class ToolExecuteBody(BaseModel):
    """POST /v1/tools/{name}:execute 的请求体。

    HTTP 层只做形状校验(422);工具参数语义校验走 common.tools.validate_arguments,
    失败仍返回 HTTP 200 + 错误信封。
    """

    model_config = ConfigDict(extra="forbid")

    tool_call_id: str = Field(min_length=1, max_length=TOOL_CALL_ID_MAX)
    arguments: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = Field(default=None, max_length=CONTEXT_ID_MAX)
    conversation_id: str | None = Field(default=None, max_length=CONTEXT_ID_MAX)
    workspace_id: str | None = Field(default=None, max_length=CONTEXT_ID_MAX)
    sandbox_scope: str | None = Field(default=None, max_length=SCOPE_KEY_MAX)
    timeout_seconds: float | None = Field(default=None, ge=1, le=EXECUTE_TIMEOUT_MAX)
    approved: bool = False


class ToolDescriptorRead(BaseModel):
    """对外展示的工具描述(与 wire ToolDescriptor 字段一一对应)。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    risk: str
    kind: str
    executor: str
    timeout_seconds: float
    scopes: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    enabled: bool
    version: str

    @classmethod
    def from_descriptor(cls, descriptor: ToolDescriptor) -> ToolDescriptorRead:
        return cls(
            name=descriptor.name,
            description=descriptor.description,
            input_schema=descriptor.input_schema,
            output_schema=descriptor.output_schema,
            risk=str(descriptor.risk),
            kind=str(descriptor.kind),
            executor=str(descriptor.executor),
            timeout_seconds=descriptor.timeout_seconds,
            scopes=list(descriptor.scopes),
            aliases=list(descriptor.aliases),
            enabled=descriptor.enabled,
            version=descriptor.version,
        )


class ToolListRead(BaseModel):
    """GET /v1/tools 的响应:目录快照 + 版本(便于客户端缓存与审计对账)。"""

    tools: list[ToolDescriptorRead]
    catalog_version: str


class ToolCallRead(BaseModel):
    """工具调用审计记录(运维查询用)。"""

    id: str
    tool_call_id: str
    run_id: str | None = None
    conversation_id: str | None = None
    tool_name: str
    tool_version: str
    risk: str
    decision: str
    ok: bool
    error_code: str | None = None
    duration_ms: int
    result_chars: int
    bytes_written: int = 0
    truncated: bool
    sha256_before: str | None = None
    sha256_after: str | None = None
    paths: list[str] = Field(default_factory=list)
    # 服务端补丁:默认不回包(列表/详情都要显式 include_patch=true)。
    patch: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    patch_truncated: bool = False
    sandbox_id: str | None = None
    created_at: datetime


class ToolCallListRead(BaseModel):
    """GET /v1/tool-calls 的响应(分页)。"""

    items: list[ToolCallRead]
    total: int
    limit: int
    offset: int
