"""tool-service 客户端的共享契约:错误码、超时余量与错误信封映射。

自 tool_client.py 拆出。

HTTP 客户端、目录缓存、远端工具箱与合并视图都按这里的口径把上游故障翻成
工具错误信封:传输层不可达抛 ``UpstreamError``,其余(非 JSON/信封非法/HTTP
错误码)一律降级为 ``retryable`` 的错误 JSON 回喂模型。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from common.errors import UpstreamError
from common.tools import ToolError, ToolExecuteResponse

TokenProvider = Callable[[str], Awaitable[str]]

#: tool-service 不可达(网络/超时/DNS)。
UNREACHABLE_CODE = "tool_service.unreachable"
#: tool-service 响应不符合契约(非 JSON/信封非法/HTTP 5xx)。
UPSTREAM_ERROR_CODE = "tool_service.upstream_error"

#: 工具服务调用超时 = 服务端工具超时 + 这段余量(传输/排队)。
DEFAULT_TIMEOUT_GRACE_SECONDS = 1.0


def _decode_json(response: httpx.Response, *, expected: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError(
            UPSTREAM_ERROR_CODE,
            f"tool-service {expected}响应不是 JSON(HTTP {response.status_code})",
        ) from exc
    if response.status_code >= 400:
        error = _error_from_http(payload, status=response.status_code)
        raise UpstreamError(error.code, error.message)
    if not isinstance(payload, dict):
        raise UpstreamError(
            UPSTREAM_ERROR_CODE, f"tool-service {expected}响应必须是 JSON 对象"
        )
    return payload


def _error_from_http(payload: Any, *, status: int) -> ToolError:
    """把 tool-service 的 API 错误体映射成工具错误信封。"""

    if isinstance(payload, dict) and isinstance(payload.get("code"), str):
        return ToolError(
            code=str(payload["code"]),
            message=str(
                payload.get("message") or f"tool-service 调用失败(HTTP {status})"
            ),
            retryable=status >= 500,
        )
    return ToolError(
        code=UPSTREAM_ERROR_CODE,
        message=f"tool-service 调用失败(HTTP {status})",
        retryable=status >= 500,
    )


def _error_envelope(
    code: str,
    message: str,
    *,
    hint: str = "",
    retryable: bool = False,
    tool_call_id: str = "",
) -> str:
    response = ToolExecuteResponse(
        ok=False,
        error=ToolError(code=code, message=message, hint=hint, retryable=retryable),
        tool_call_id=tool_call_id,
    )
    return json.dumps(response.envelope(), ensure_ascii=False)
