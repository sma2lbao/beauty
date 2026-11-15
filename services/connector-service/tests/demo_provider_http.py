"""demo provider 的上游 HTTP 读取与错误映射(测试专用假上游)。"""

from __future__ import annotations

from typing import Any

import httpx

from connector.providers.base import Credentials
from connector.providers.errors import ProviderError

# 需要重新授权(token 失效/被撤销)——连接置 expired,前端展示「重新连接」。
_AUTH_ERROR_CODES = frozenset({20005, 20037, 20064})
# 应用没申请/没授予 scope——同样需要重新授权。
_SCOPE_ERROR_CODES = frozenset({20010, 20027, 20068})
# 上游网关限流(HTTP 可能是 400 + 业务码,也可能直接 429)。
_RATE_LIMIT_CODES = frozenset({99991400})


class _HttpMixin:
    """带鉴权的 GET,以及按状态码/业务码分层的错误信封。"""

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        creds: Credentials,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {creds.access_token}"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "connector.upstream_timeout",
                "上游接口超时",
                hint="稍后重试",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "connector.upstream_unavailable",
                "上游接口不可达",
                hint="稍后重试",
                retryable=True,
            ) from exc
        payload = _json_or_none(response)
        if response.status_code >= 400 or _has_error(payload):
            raise _error_from_response(response, payload)
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else {}


def _json_or_none(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _has_error(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    code = payload.get("code")
    return code is not None and code != 0


def _int_code(payload: dict[str, Any] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    try:
        return int(payload.get("code"))
    except (TypeError, ValueError):
        return None


def _error_from_response(
    response: httpx.Response, payload: dict[str, Any] | None
) -> ProviderError:
    status = response.status_code
    code = _int_code(payload)
    label = code if code is not None else status
    if status == 429 or code in _RATE_LIMIT_CODES:
        retry_after = _retry_after(response)
        hint = (
            f"上游限流,建议 {retry_after} 秒后重试"
            if retry_after
            else "上游限流,请稍后重试"
        )
        return ProviderError(
            "connector.upstream_rate_limited",
            f"上游接口限流(代码 {label})",
            hint=hint,
            retryable=True,
        )
    if status == 403 or code in _SCOPE_ERROR_CODES:
        return ProviderError(
            "connector.scope_missing",
            f"应用缺少所需权限(代码 {label})",
            hint="请补充 scope 并重新授权",
        )
    if status == 401 or code in _AUTH_ERROR_CODES:
        return ProviderError(
            "connector.auth_expired",
            f"授权已失效(代码 {label})",
            hint="请重新连接",
        )
    if status >= 500:
        return ProviderError(
            "connector.upstream_unavailable",
            f"上游接口暂不可用(HTTP {status})",
            hint="稍后重试",
            retryable=True,
        )
    return ProviderError(
        "connector.upstream_error",
        f"上游接口返回错误(代码 {label})",
        hint="检查参数或稍后重试",
    )


def _retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("x-ogw-ratelimit-reset") or response.headers.get(
        "retry-after"
    )
    try:
        value = int(float(str(raw)))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
