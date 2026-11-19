"""GitHub REST/OAuth 的 HTTP 封装与错误映射(provider 内部模块)。

只放「怎么发请求、怎么把上游错误翻成 ProviderError」,不放工具语义;
工具参数校验与结果整形在 github_tools.py,授权/身份在 github.py。
"""

from __future__ import annotations

import base64
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from connector.providers.base import Credentials
from connector.providers.errors import ProviderError

API_BASE = "https://api.github.com"
AUTH_BASE = "https://github.com"
TOKEN_URL = f"{AUTH_BASE}/login/oauth/access_token"
API_VERSION = "2022-11-28"
#: 文件内容/diff 最多带回的字符数;超过只回截断片段 + truncated 标记。
CONTENT_LIMIT = 256 * 1024

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class _HttpMixin:
    """带鉴权的 GitHub 请求,以及按状态码/限流头分层的错误信封。"""

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        creds: Credentials,
        *,
        params: dict[str, Any] | None = None,
        accept: str | None = None,
    ) -> Any:
        response = await self._request(
            client, "GET", url, creds, params=params, accept=accept
        )
        payload = _json_or_none(response)
        if not isinstance(payload, (dict, list)):
            raise ProviderError(
                "connector.upstream_error", "GitHub 返回了无法解析的响应"
            )
        return payload

    async def _get_text(
        self,
        client: httpx.AsyncClient,
        url: str,
        creds: Credentials,
        *,
        accept: str | None = None,
    ) -> str:
        response = await self._request(client, "GET", url, creds, accept=accept)
        return response.text

    async def _post_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        creds: Credentials,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._request(client, "POST", url, creds, body=body)
        payload = _json_or_none(response)
        return payload if isinstance(payload, dict) else {}

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        creds: Credentials,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        accept: str | None = None,
    ) -> httpx.Response:
        try:
            response = await client.request(
                method,
                url,
                params=params,
                json=body,
                headers=_headers(creds, accept),
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "connector.upstream_timeout",
                "GitHub 接口超时",
                hint="稍后重试",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "connector.upstream_unavailable",
                "GitHub 接口不可达",
                hint="稍后重试",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise _error_from_response(response, _json_or_none(response))
        return response

    async def _token_request(
        self,
        client: httpx.AsyncClient,
        body: dict[str, Any],
        *,
        refresh: bool,
    ) -> dict[str, Any]:
        """POST token 端点;GitHub 的错误常是 HTTP 200 + body.error。"""
        try:
            response = await client.post(
                TOKEN_URL,
                data=body,
                headers=_token_headers(),
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "connector.upstream_timeout",
                "GitHub 授权服务超时",
                hint="稍后重试",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "connector.upstream_unavailable",
                "GitHub 授权服务不可达",
                hint="稍后重试",
                retryable=True,
            ) from exc
        payload = _json_or_none(response)
        error = payload.get("error") if isinstance(payload, dict) else None
        if response.status_code >= 400 or error:
            raise _token_error(response, payload, refresh=refresh)
        if not isinstance(payload, dict):
            raise ProviderError("connector.oauth_failed", "GitHub 未返回 JSON")
        return payload


def _headers(creds: Credentials, accept: str | None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {creds.access_token}",
        "Accept": accept or "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "beauty-moon-connector",
    }


def _token_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": "beauty-moon-connector",
    }


def _error_from_response(response: httpx.Response, payload: Any) -> ProviderError:
    """§6:REST 错误映射;上游原文只做限长摘要,不整段回显。"""
    status = response.status_code
    if status == 401:
        return ProviderError(
            "connector.auth_expired",
            "GitHub 授权已失效",
            hint="请重新连接",
        )
    if status == 403:
        if _is_rate_limited(response, payload):
            return _rate_limited(response)
        return ProviderError(
            "connector.upstream_error",
            "GitHub 无权限或资源受限",
            hint="无权限或资源受限",
        )
    if status == 429 or response.headers.get("retry-after"):
        return _rate_limited(response)
    if status == 404:
        return ProviderError(
            "connector.invalid_arguments",
            "GitHub 资源不存在",
            hint="仓库/路径/编号不存在或无权访问",
        )
    if status == 422:
        message = _github_message(payload)
        return ProviderError(
            "connector.invalid_arguments",
            "GitHub 参数不合法",
            hint=f"参数不合法: {message}" if message else "参数不合法",
        )
    if status >= 500:
        return ProviderError(
            "connector.upstream_unavailable",
            f"GitHub 暂不可用(HTTP {status})",
            hint="稍后重试",
            retryable=True,
        )
    return ProviderError(
        "connector.upstream_error",
        f"GitHub 返回错误(HTTP {status})",
        hint="检查参数或稍后重试",
    )


def _token_error(
    response: httpx.Response, payload: Any, *, refresh: bool
) -> ProviderError:
    """token 端点错误:只有「refresh token 失效」才让连接过期。"""
    status = response.status_code
    error = ""
    if isinstance(payload, dict):
        error = str(payload.get("error") or "")
    if status == 429 or response.headers.get("retry-after"):
        return _rate_limited(response)
    if status >= 500:
        return ProviderError(
            "connector.upstream_unavailable",
            f"GitHub 授权服务暂不可用(HTTP {status})",
            hint="稍后重试",
            retryable=True,
        )
    if error == "incorrect_client_credentials":
        return ProviderError(
            "connector.oauth_failed",
            "GitHub App 凭证不正确",
            hint="检查 client_id/client_secret 配置",
        )
    if refresh and error in {"bad_refresh_token", "invalid_grant"}:
        return ProviderError(
            "connector.auth_expired",
            "GitHub refresh token 已失效",
            hint="请重新连接",
        )
    if refresh:
        return ProviderError(
            "connector.auth_expired",
            "GitHub 刷新授权失败",
            hint="请重新连接",
        )
    return ProviderError(
        "connector.oauth_failed",
        "GitHub 换码失败",
        hint="授权码无效、已使用或 redirect_uri 不匹配",
    )


def _is_rate_limited(response: httpx.Response, payload: Any) -> bool:
    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining is not None and remaining.strip() == "0":
        return True
    if response.headers.get("retry-after"):
        return True
    message = _github_message(payload).lower()
    return "rate limit" in message or "secondary rate limit" in message


def _rate_limited(response: httpx.Response) -> ProviderError:
    return ProviderError(
        "connector.upstream_rate_limited",
        "GitHub 接口限流",
        hint=_rate_limit_hint(response),
        retryable=True,
    )


def _rate_limit_hint(response: httpx.Response) -> str:
    retry_after = _int_header(response, "retry-after")
    if retry_after is not None:
        return f"GitHub 限流,建议 {retry_after} 秒后重试"
    reset = _int_header(response, "x-ratelimit-reset")
    if reset is not None:
        wait = max(0, reset - int(time.time()))
        return f"GitHub 限流,建议 {wait} 秒后重试"
    return "GitHub 限流,请稍后重试"


def _int_header(response: httpx.Response, name: str) -> int | None:
    raw = response.headers.get(name)
    try:
        value = int(float(str(raw)))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _github_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    message = payload.get("message")
    if not isinstance(message, str):
        return ""
    return message.strip()[:200]


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


# ── 参数校验与 URL 片段 ──────────────────────────────────────────────────


def repo_parts(arguments: dict[str, Any]) -> tuple[str, str]:
    """repo 统一 "owner/name";格式非法一律 invalid_arguments。"""
    raw = require_str(arguments, "repo")
    if raw.count("/") != 1:
        raise ProviderError(
            "connector.invalid_arguments",
            "repo 参数格式应为 owner/name",
        )
    owner, repo = raw.split("/")
    if not _OWNER_RE.match(owner) or not _REPO_RE.match(repo):
        raise ProviderError(
            "connector.invalid_arguments",
            "repo 参数格式应为 owner/name",
        )
    return owner, repo


def repo_url(owner: str, repo: str, suffix: str = "") -> str:
    return f"{API_BASE}/repos/{quote(owner)}/{quote(repo)}{suffix}"


def paging(
    arguments: dict[str, Any],
    *,
    default_per_page: int = 30,
    max_per_page: int = 100,
) -> dict[str, int]:
    page = int_arg(arguments.get("page"), default=1, name="page")
    per_page = int_arg(
        arguments.get("per_page"), default=default_per_page, name="per_page"
    )
    if page < 1 or per_page < 1:
        raise ProviderError("connector.invalid_arguments", "page/per_page 必须为正整数")
    return {"page": page, "per_page": min(per_page, max_per_page)}


def int_arg(value: Any, *, default: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ProviderError("connector.invalid_arguments", f"{name} 必须为整数")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderError(
            "connector.invalid_arguments", f"{name} 必须为整数"
        ) from exc


def require_int(arguments: dict[str, Any], key: str) -> int:
    value = arguments.get(key)
    if value is None or isinstance(value, bool):
        raise ProviderError("connector.invalid_arguments", f"缺少必填参数: {key}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderError(
            "connector.invalid_arguments", f"{key} 必须为正整数"
        ) from exc
    if number < 1:
        raise ProviderError("connector.invalid_arguments", f"{key} 必须为正整数")
    return number


def require_str(
    arguments: dict[str, Any], key: str, *, max_len: int | None = None
) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderError("connector.invalid_arguments", f"缺少必填参数: {key}")
    text = value.strip()
    if max_len is not None and len(text) > max_len:
        raise ProviderError(
            "connector.invalid_arguments", f"{key} 长度不能超过 {max_len}"
        )
    return text


def optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def bool_arg(value: Any) -> bool:
    return bool(value)


def state_arg(arguments: dict[str, Any], *, default: str = "open") -> str:
    state = optional_str(arguments.get("state")) or default
    if state not in {"open", "closed", "all"}:
        raise ProviderError(
            "connector.invalid_arguments",
            "state 只能是 open/closed/all",
        )
    return state


def labels_arg(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProviderError("connector.invalid_arguments", "labels 必须是字符串数组")
    labels: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProviderError(
                "connector.invalid_arguments", "labels 必须是字符串数组"
            )
        labels.append(item.strip())
    return labels


def clean_path(arguments: dict[str, Any]) -> str:
    if "path" not in arguments:
        raise ProviderError("connector.invalid_arguments", "缺少必填参数: path")
    raw = arguments["path"]
    if not isinstance(raw, str):
        raise ProviderError("connector.invalid_arguments", "path 必须是字符串")
    path = raw.strip().lstrip("/")
    if any(part == ".." for part in path.split("/")):
        raise ProviderError("connector.invalid_arguments", "path 不能包含 ..")
    return quote(path, safe="/")


def truncate(text: str, limit: int = CONTENT_LIMIT) -> tuple[str, bool]:
    """按 UTF-8 字节截断,避免中文内容超过 256KB 的承诺。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="ignore"), True


def decode_content(payload: dict[str, Any]) -> tuple[str, bool, str | None]:
    """GitHub contents API 的 base64 解码;大文件/非 base64 显式标记。"""
    content = payload.get("content")
    encoding = payload.get("encoding")
    if encoding == "base64" and isinstance(content, str):
        try:
            raw = base64.b64decode(content, validate=False)
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                "connector.upstream_error", "GitHub 文件内容无法解码"
            ) from exc
        return _decode_text(raw, payload.get("size"))
    size = payload.get("size")
    note = "文件过大或非 base64 编码,GitHub 未返回正文"
    if isinstance(size, int) and size > 1024 * 1024:
        note = "文件超过 1MB,GitHub 不返回 base64 正文"
    return "", True, note


def _decode_text(raw: bytes, size: Any) -> tuple[str, bool, str | None]:
    text = raw.decode("utf-8", errors="replace")
    text, truncated = truncate(text)
    if truncated:
        return text, True, "内容超过 256KB,已截断"
    if isinstance(size, int) and size > len(raw):
        return text, True, "GitHub 只返回了部分内容"
    return text, False, None
