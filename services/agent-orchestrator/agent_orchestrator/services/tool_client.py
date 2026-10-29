"""tool-service 的 HTTP 客户端与远端工具箱适配器(设计 §12.3)。

orchestrator 不再在本进程里注册内置工具:工具目录与执行都委托给
tool-service(:8008),本模块负责三件事:

- ToolClient:GET /v1/tools 拉目录、POST /v1/tools/{name}:execute 执行;
- ToolCatalog:进程级目录缓存(首次使用拉一次,失败下次重试;refresh 显式刷新);
- RemoteToolkit / HybridToolkit:实现 graph.ports.ToolkitPort,保持旧
  ToolRegistry 的 OpenAI schema 形状与「执行永不抛、错误 JSON 回喂」契约,
  并把三档风险映射回旧枚举(read/write/danger)供审批闸使用;execute() 的
  approved/tool_call_id 原样透传给 tool-service——审批凭据由调用方给出,
  不再按风险自动补 approved(P1 受控写)。

服务不可用时的口径:目录拉不到 -> ensure_ready 抛 UpstreamError(装配方
按必需依赖处理,不静默降级到本机直读);执行期不可达 -> 回喂错误信封
(retryable=true),让模型决策,不炸整条 run。

拆分后的实现见同目录兄弟模块:``tool_client_http``(HTTP 客户端)、
``tool_client_catalog``(目录缓存)、``tool_client_remote``(远端工具箱)、
``tool_client_hybrid``(远端 + 本地合并视图)、``tool_client_support``(错误码、
超时余量与错误信封映射)。本文件只做重导出,调用方继续按
``services.tool_client.<name>`` 取用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from common.errors import UpstreamError
from common.tools import (
    ToolDescriptor,
    ToolError,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolKind,
    ToolRisk,
    to_legacy_risk,
    validate_arguments,
)

from agent_orchestrator.services.tool_client_catalog import ToolCatalog
from agent_orchestrator.services.tool_client_http import ToolClient
from agent_orchestrator.services.tool_client_hybrid import HybridToolkit, _contains
from agent_orchestrator.services.tool_client_remote import RemoteToolkit
from agent_orchestrator.services.tool_client_support import (
    DEFAULT_TIMEOUT_GRACE_SECONDS,
    UNREACHABLE_CODE,
    UPSTREAM_ERROR_CODE,
    TokenProvider,
    _decode_json,
    _error_envelope,
    _error_from_http,
)
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import ToolRegistry, ToolSpec

#: 对外保留的历史名字(调用方与测试按 ``services.tool_client.<name>`` 取用)。
__all__ = [
    "Any",
    "Awaitable",
    "Callable",
    "ToolDescriptor",
    "ToolError",
    "ToolExecuteRequest",
    "ToolExecuteResponse",
    "ToolKind",
    "ToolRegistry",
    "ToolRisk",
    "ToolSpec",
    "UpstreamError",
    "current_tool_context",
    "to_legacy_risk",
    "validate_arguments",
    "DEFAULT_TIMEOUT_GRACE_SECONDS",
    "HybridToolkit",
    "RemoteToolkit",
    "TokenProvider",
    "ToolCatalog",
    "ToolClient",
    "UNREACHABLE_CODE",
    "UPSTREAM_ERROR_CODE",
    "_contains",
    "_decode_json",
    "_error_envelope",
    "_error_from_http",
]
