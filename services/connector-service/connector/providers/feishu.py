"""飞书 provider:企业自建应用 + 用户身份(user_access_token)+ 只读工具。

授权:accounts.feishu.cn 的 OAuth v3(PKCE S256,必须 offline_access 才发
refresh_token);工具:drive/docx/wiki 只读 6 个。凭证只在本进程内存里出现,
落库前由 service 层信封加密。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar
from urllib.parse import urlencode

import httpx

from connector.core.clock import utcnow
from connector.core.config import Settings
from connector.providers.base import (
    BaseProvider,
    Credentials,
    Identity,
    ToolSpec,
)
from connector.providers.errors import ProviderError

# 需要重新授权(刷新失败/ token 失效)——连接置 expired,前端展示「重新连接」。
_AUTH_ERROR_CODES = frozenset(
    {20005, 20037, 20064, 99991661, 99991663, 99991668, 99991677}
)
# 应用没申请/没授予 scope——同样需要重新授权,但文案指向开放平台权限配置。
_SCOPE_ERROR_CODES = frozenset({20010, 20027, 20068})
# 飞书网关限流(HTTP 可能是 400 + 业务码 99991400,也可能直接 429)。
_RATE_LIMIT_CODES = frozenset({99991400})

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 50


class FeishuProvider(BaseProvider):
    name: ClassVar[str] = "feishu"
    prefix: ClassVar[str] = "Feishu"
    display_name: ClassVar[str] = "飞书"
    description: ClassVar[str] = "以你的飞书身份只读访问云文档、知识库与云空间文件"
    docs_url: ClassVar[str] = "https://open.feishu.cn/document"
    tools: ClassVar[tuple[ToolSpec, ...]] = (
        ToolSpec(
            name="get_me",
            title="我的飞书身份",
            description="返回当前授权用户的 open_id、姓名与租户标识。",
            input_schema={"type": "object", "properties": {}},
        ),
        ToolSpec(
            name="list_files",
            title="列出云空间文件",
            description="列出飞书云空间(我的空间或指定文件夹)下的文件与文件夹。",
            input_schema={
                "type": "object",
                "properties": {
                    "folder_token": {
                        "type": "string",
                        "description": "文件夹 token;不填为根目录",
                    },
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
                    "page_token": {"type": "string", "description": "分页 token"},
                    "order_by": {
                        "type": "string",
                        "enum": ["EditedTime", "CreatedTime"],
                        "description": "排序方式,默认 EditedTime",
                    },
                },
            },
        ),
        ToolSpec(
            name="read_document",
            title="读取文档正文",
            description="按文档 token 读取飞书文档的纯文本正文(raw_content)。",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": "文档 token(docx 的 document_id)",
                    }
                },
                "required": ["document_id"],
            },
        ),
        ToolSpec(
            name="list_document_blocks",
            title="读取文档块结构",
            description="按文档 token 读取文档块(段落/标题/表格等)的结构与内容。",
            input_schema={
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
                    "page_token": {"type": "string"},
                },
                "required": ["document_id"],
            },
        ),
        ToolSpec(
            name="list_wiki_spaces",
            title="列出知识库",
            description="列出当前用户可见的飞书知识库(wiki spaces)。",
            input_schema={
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
                    "page_token": {"type": "string"},
                },
            },
        ),
        ToolSpec(
            name="list_wiki_nodes",
            title="列出知识库节点",
            description="列出知识库下的节点(文档/表格/文件夹),可递归子节点。",
            input_schema={
                "type": "object",
                "properties": {
                    "space_id": {"type": "string"},
                    "parent_node_token": {"type": "string"},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
                    "page_token": {"type": "string"},
                },
                "required": ["space_id"],
            },
        ),
    )

    # ---- 应用凭证 ----

    def __init__(
        self,
        settings: Settings,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
    ) -> None:
        """应用凭证默认取全局 Settings;租户自带应用经 with_tenant_app 覆盖。"""
        super().__init__(settings)
        self._app_id = app_id or settings.feishu_app_id
        self._app_secret = app_secret or settings.feishu_app_secret

    def with_tenant_app(self, app_id: str, app_secret: str) -> FeishuProvider:
        """用 tenant_apps 行的应用凭证构造副本;secret 只在内存,不落日志。"""
        return FeishuProvider(self.settings, app_id=app_id, app_secret=app_secret)

    # ---- 配置 ----

    @property
    def configured(self) -> bool:
        return bool(self._app_id and self._app_secret)

    def scopes(self) -> tuple[str, ...]:
        return tuple(scope for scope in self.settings.feishu_scopes.split() if scope)

    @property
    def _open_api(self) -> str:
        return self.settings.feishu_open_api_base.rstrip("/")

    @property
    def _accounts(self) -> str:
        return self.settings.feishu_accounts_base.rstrip("/")

    # ---- 授权 ----

    def authorize_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        query = urlencode(
            {
                "client_id": self._app_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.scopes()),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self._accounts}/open-apis/authen/v1/authorize?{query}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client: httpx.AsyncClient,
    ) -> Credentials:
        payload = await self._token_request(
            client,
            {
                "grant_type": "authorization_code",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )
        return self._credentials_from_token(payload)

    async def refresh(
        self, credentials: Credentials, client: httpx.AsyncClient
    ) -> Credentials:
        if not credentials.refresh_token:
            raise ProviderError(
                "connector.auth_expired",
                "飞书连接没有 refresh token,需要重新授权",
                hint="请重新连接飞书",
            )
        payload = await self._token_request(
            client,
            {
                "grant_type": "refresh_token",
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "refresh_token": credentials.refresh_token,
            },
        )
        return self._credentials_from_token(payload)

    async def _token_request(
        self, client: httpx.AsyncClient, body: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"{self._accounts}/oauth/v3/token"
        try:
            response = await client.post(url, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "connector.upstream_timeout",
                "飞书授权服务超时",
                hint="稍后重试",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "connector.upstream_unavailable",
                "飞书授权服务不可达",
                hint="稍后重试",
                retryable=True,
            ) from exc
        payload = _json_or_none(response)
        if response.status_code >= 400 or _has_error(payload):
            raise self._token_error(response, payload)
        return payload or {}

    def _credentials_from_token(self, payload: dict[str, Any]) -> Credentials:
        access_token = payload.get("access_token")
        if not access_token:
            raise ProviderError("connector.oauth_failed", "飞书未返回 access_token")
        return Credentials(
            access_token=str(access_token),
            refresh_token=payload.get("refresh_token"),
            token_type=str(payload.get("token_type") or "Bearer"),
            scope=str(payload.get("scope") or ""),
            expires_at=_expiry(payload.get("expires_in")),
            refresh_expires_at=_expiry(payload.get("refresh_token_expires_in")),
        )

    def _token_error(
        self, response: httpx.Response, payload: dict[str, Any] | None
    ) -> ProviderError:
        """授权端点的错误分层:限流/上游故障不得被当成"需要重新授权"。

        token 端点的 429 与 5xx 都是瞬时的:若映射成 auth_expired,连接会被
        置 expired(REAUTH_ERROR_CODES),一次网络抖动就要用户重新扫码。
        """
        code = _int_code(payload)
        label = code if code is not None else response.status_code
        status = response.status_code
        if status == 429 or code in _RATE_LIMIT_CODES:
            retry_after = _retry_after(response)
            hint = (
                f"飞书授权服务限流,建议 {retry_after} 秒后重试"
                if retry_after
                else "飞书授权服务限流,请稍后重试"
            )
            return ProviderError(
                "connector.upstream_rate_limited",
                f"飞书授权服务限流(代码 {label})",
                hint=hint,
                retryable=True,
            )
        if status >= 500:
            return ProviderError(
                "connector.upstream_unavailable",
                f"飞书授权服务暂不可用(HTTP {status})",
                hint="稍后重试",
                retryable=True,
            )
        if code in _AUTH_ERROR_CODES or status in (400, 401, 403):
            return ProviderError(
                "connector.auth_expired",
                f"飞书授权已失效(代码 {label})",
                hint="请重新连接飞书",
            )
        return ProviderError(
            "connector.oauth_failed",
            f"飞书授权失败(代码 {label})",
            hint="请重新发起授权",
        )

    # ---- 身份 ----

    async def fetch_identity(
        self, credentials: Credentials, client: httpx.AsyncClient
    ) -> Identity:
        data = await self._get(
            client,
            f"{self._open_api}/open-apis/authen/v1/user_info",
            credentials,
        )
        open_id = data.get("open_id")
        if not open_id:
            raise ProviderError("connector.upstream_error", "飞书未返回用户 open_id")
        return Identity(
            account=str(open_id),
            display_name=data.get("name") or data.get("en_name"),
            provider_account_id=str(open_id),
            external_workspace=data.get("tenant_key"),
            metadata={
                "open_id": open_id,
                "union_id": data.get("union_id"),
                "name": data.get("name"),
                "avatar_url": data.get("avatar_url"),
            },
        )

    # ---- 工具 ----

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        credentials: dict[str, Any],
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        spec = self.tool_spec(tool_name)
        creds = Credentials.from_payload(credentials)
        handler = getattr(self, f"_call_{spec.name}")
        return await handler(arguments, creds, client)

    async def _call_get_me(
        self, arguments: dict[str, Any], creds: Credentials, client: httpx.AsyncClient
    ) -> dict[str, Any]:
        identity = await self.fetch_identity(creds, client)
        return {
            "open_id": identity.provider_account_id,
            "name": identity.display_name,
            "tenant_key": identity.external_workspace,
            "union_id": identity.metadata.get("union_id"),
            "avatar_url": identity.metadata.get("avatar_url"),
        }

    async def _call_list_files(
        self, arguments: dict[str, Any], creds: Credentials, client: httpx.AsyncClient
    ) -> dict[str, Any]:
        params = _paging(arguments)
        folder_token = _opt_str(arguments.get("folder_token"))
        if folder_token:
            params["folder_token"] = folder_token
        order_by = _opt_str(arguments.get("order_by"))
        if order_by:
            params["order_by"] = order_by
        data = await self._get(
            client, f"{self._open_api}/open-apis/drive/v1/files", creds, params=params
        )
        return {
            "files": [
                {
                    "token": item.get("token"),
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "url": item.get("url"),
                    "parent_token": item.get("parent_token"),
                }
                for item in data.get("files", [])
                if isinstance(item, dict)
            ],
            "has_more": bool(data.get("has_more")),
            "page_token": data.get("next_page_token"),
        }

    async def _call_read_document(
        self, arguments: dict[str, Any], creds: Credentials, client: httpx.AsyncClient
    ) -> dict[str, Any]:
        document_id = _require_str(arguments, "document_id")
        data = await self._get(
            client,
            f"{self._open_api}/open-apis/docx/v1/documents/{document_id}/raw_content",
            creds,
        )
        return {"document_id": document_id, "content": data.get("content", "")}

    async def _call_list_document_blocks(
        self, arguments: dict[str, Any], creds: Credentials, client: httpx.AsyncClient
    ) -> dict[str, Any]:
        document_id = _require_str(arguments, "document_id")
        data = await self._get(
            client,
            f"{self._open_api}/open-apis/docx/v1/documents/{document_id}/blocks",
            creds,
            params=_paging(arguments),
        )
        return {
            "items": data.get("items", []),
            "has_more": bool(data.get("has_more")),
            "page_token": data.get("page_token"),
        }

    async def _call_list_wiki_spaces(
        self, arguments: dict[str, Any], creds: Credentials, client: httpx.AsyncClient
    ) -> dict[str, Any]:
        data = await self._get(
            client,
            f"{self._open_api}/open-apis/wiki/v2/spaces",
            creds,
            params=_paging(arguments),
        )
        return {
            "items": [
                {
                    "space_id": item.get("space_id"),
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "space_type": item.get("space_type"),
                    "visibility": item.get("visibility"),
                }
                for item in data.get("items", [])
                if isinstance(item, dict)
            ],
            "has_more": bool(data.get("has_more")),
            "page_token": data.get("page_token"),
        }

    async def _call_list_wiki_nodes(
        self, arguments: dict[str, Any], creds: Credentials, client: httpx.AsyncClient
    ) -> dict[str, Any]:
        space_id = _require_str(arguments, "space_id")
        params = _paging(arguments)
        parent = _opt_str(arguments.get("parent_node_token"))
        if parent:
            params["parent_node_token"] = parent
        data = await self._get(
            client,
            f"{self._open_api}/open-apis/wiki/v2/spaces/{space_id}/nodes",
            creds,
            params=params,
        )
        return {
            "items": [
                {
                    "node_token": item.get("node_token"),
                    "obj_token": item.get("obj_token"),
                    "obj_type": item.get("obj_type"),
                    "parent_node_token": item.get("parent_node_token"),
                    "title": item.get("title"),
                    "has_child": item.get("has_child"),
                }
                for item in data.get("items", [])
                if isinstance(item, dict)
            ],
            "has_more": bool(data.get("has_more")),
            "page_token": data.get("page_token"),
        }

    # ---- HTTP ----

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
                "飞书接口超时",
                hint="稍后重试",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "connector.upstream_unavailable",
                "飞书接口不可达",
                hint="稍后重试",
                retryable=True,
            ) from exc
        payload = _json_or_none(response)
        if response.status_code >= 400 or _has_error(payload):
            raise _error_from_response(response, payload)
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else {}


def _paging(arguments: dict[str, Any]) -> dict[str, Any]:
    raw = arguments.get("page_size", _PAGE_SIZE_DEFAULT)
    try:
        page_size = int(raw)
    except (TypeError, ValueError):
        page_size = _PAGE_SIZE_DEFAULT
    page_size = max(1, min(_PAGE_SIZE_MAX, page_size))
    params: dict[str, Any] = {"page_size": page_size}
    page_token = _opt_str(arguments.get("page_token"))
    if page_token:
        params["page_token"] = page_token
    return params


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderError("connector.invalid_arguments", f"缺少必填参数: {key}")
    return value.strip()


def _opt_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _expiry(raw: Any):
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return utcnow() + timedelta(seconds=seconds)


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
            f"飞书限流,建议 {retry_after} 秒后重试"
            if retry_after
            else "飞书限流,请稍后重试"
        )
        return ProviderError(
            "connector.upstream_rate_limited",
            f"飞书接口限流(代码 {label})",
            hint=hint,
            retryable=True,
        )
    if status == 403 or code in _SCOPE_ERROR_CODES:
        return ProviderError(
            "connector.scope_missing",
            f"飞书应用缺少所需权限(代码 {label})",
            hint="请在飞书开放平台补充 scope 并重新授权",
        )
    if status == 401 or code in _AUTH_ERROR_CODES:
        return ProviderError(
            "connector.auth_expired",
            f"飞书授权已失效(代码 {label})",
            hint="请重新连接飞书",
        )
    if status >= 500:
        return ProviderError(
            "connector.upstream_unavailable",
            f"飞书接口暂不可用(HTTP {status})",
            hint="稍后重试",
            retryable=True,
        )
    return ProviderError(
        "connector.upstream_error",
        f"飞书接口返回错误(代码 {label})",
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


def build_feishu_provider(settings: Settings) -> FeishuProvider:
    return FeishuProvider(settings)
