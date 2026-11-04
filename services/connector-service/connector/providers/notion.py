"""Notion provider:public integration OAuth(bot token)+ 只读/写工具。

授权走 Notion 的 OAuth2:授权页带 `owner=user`(固定值),不支持 PKCE,换 token
用 HTTP Basic Auth;token 响应不含 `expires_in`(TTL 未知),所以不做按时间惰性
刷新,401 视为授权失效;有 refresh_token 时支持手动刷新。

工具分两档(风险等级随 ToolDescriptor 透出给 tool-service):只读 search /
read_page / read_blocks / query_data_source;写入 create_page / append_blocks
标 `ToolRisk.WRITE`,执行前必须经对话或任务的人工确认(approved=True)。
凭证只在本进程内存里出现,落库前由 service 层信封加密。
"""

from __future__ import annotations

import base64
from typing import Any, ClassVar
from urllib.parse import quote, urlencode

import httpx
from common.tools import ToolRisk

from connector.core.config import Settings
from connector.providers.base import (
    BaseProvider,
    Credentials,
    Identity,
    ToolSpec,
)
from connector.providers.errors import ProviderError

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100

_AUTHORIZE_PATH = "/v1/oauth/authorize"
_TOKEN_PATH = "/v1/oauth/token"


class NotionProvider(BaseProvider):
    name: ClassVar[str] = "notion"
    prefix: ClassVar[str] = "Notion"
    display_name: ClassVar[str] = "Notion"
    description: ClassVar[str] = "以你授权的 Notion 集成只读访问页面、数据库与内容块"
    docs_url: ClassVar[str] = "https://developers.notion.com"
    tools: ClassVar[tuple[ToolSpec, ...]] = (
        ToolSpec(
            name="search",
            title="搜索 Notion 内容",
            description=(
                "在集成有权限的工作区里搜索页面或 data source;query 为空时按最近"
                "编辑返回,结果只含 id/标题/链接等摘要。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词;不填则列出最近编辑的内容",
                    },
                    "object_type": {
                        "type": "string",
                        "enum": ["page", "data_source"],
                        "description": "只搜页面或只搜 data source;不填则都搜",
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "返回条数,默认 20",
                    },
                    "start_cursor": {
                        "type": "string",
                        "description": "分页游标(上一页的 next_cursor)",
                    },
                },
            },
        ),
        ToolSpec(
            name="read_page",
            title="读取页面属性",
            description="读取 Notion 页面标题、属性与父级信息(不含正文块)。",
            input_schema={
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "页面 id(UUID,可带或不带连字符)",
                    }
                },
                "required": ["page_id"],
            },
        ),
        ToolSpec(
            name="read_blocks",
            title="读取内容块",
            description="按 block_id(页面 id 也是根块)分页读取子块结构与内容。",
            input_schema={
                "type": "object",
                "properties": {
                    "block_id": {
                        "type": "string",
                        "description": "块 id;读整页正文时传页面 id",
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "返回条数,默认 20",
                    },
                    "start_cursor": {"type": "string", "description": "分页游标"},
                },
                "required": ["block_id"],
            },
        ),
        ToolSpec(
            name="query_data_source",
            title="查询 data source",
            description="按 data_source_id 查询数据库行,支持过滤与排序。",
            input_schema={
                "type": "object",
                "properties": {
                    "data_source_id": {
                        "type": "string",
                        "description": "data source id(UUID)",
                    },
                    "filter": {
                        "type": "object",
                        "description": "Notion 过滤条件对象(原样透传)",
                    },
                    "sorts": {
                        "type": "array",
                        "description": "排序条件数组(原样透传)",
                        "items": {"type": "object"},
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "返回条数,默认 20",
                    },
                    "start_cursor": {"type": "string", "description": "分页游标"},
                },
                "required": ["data_source_id"],
            },
        ),
        ToolSpec(
            name="create_page",
            title="新建页面",
            description=(
                "在指定页面或 data source 下新建页面并写入段落正文(写操作,"
                "执行前需要用户确认)。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "parent_type": {
                        "type": "string",
                        "enum": ["page", "data_source"],
                        "description": "父级类型:页面下建子页面,或数据源里建一行",
                    },
                    "parent_id": {
                        "type": "string",
                        "description": "父级页面或 data source 的 id",
                    },
                    "title": {
                        "type": "string",
                        "description": "新页面标题(写入 title 属性)",
                    },
                    "title_property": {
                        "type": "string",
                        "description": "标题属性名;data source 里可指定,默认 title",
                    },
                    "paragraphs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "页面正文段落,每项一段纯文本",
                    },
                },
                "required": ["parent_id", "title"],
            },
            risk=ToolRisk.WRITE,
        ),
        ToolSpec(
            name="append_blocks",
            title="追加内容块",
            description=(
                "在指定块末尾追加段落(页面本身也是块;写操作,执行前需要用户确认)。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "block_id": {
                        "type": "string",
                        "description": "块 id;追加到页面末尾时传页面 id",
                    },
                    "paragraphs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                        "description": "要追加的段落,每项一段纯文本",
                    },
                    "after": {
                        "type": "string",
                        "description": "可选:插到该块之后,不填则追加到末尾",
                    },
                },
                "required": ["block_id", "paragraphs"],
            },
            risk=ToolRisk.WRITE,
        ),
    )

    def __init__(
        self,
        settings: Settings,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        super().__init__(settings)
        self._client_id = (
            client_id if client_id is not None else settings.notion_client_id
        )
        self._client_secret = (
            client_secret
            if client_secret is not None
            else settings.notion_client_secret
        )
        self._api = settings.notion_api_base.rstrip("/")
        self._version = settings.notion_version
        # exchange_code 里的 workspace/owner 信息:Notion 的 /v1/users/me 不返回
        # workspace_id,只能从换 token 响应里拿;callback 里 exchange 与 identity
        # 用同一个 provider 实例,所以暂存在实例上。
        self._token_meta: dict[str, Any] = {}

    # ---- 租户应用凭证 ----

    def with_tenant_app(self, app_id: str, app_secret: str) -> NotionProvider:
        """租户自带 Notion 集成:app_id=client_id、app_secret=client_secret。"""
        return NotionProvider(self.settings, client_id=app_id, client_secret=app_secret)

    # ---- 静态描述 ----

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def scopes(self) -> tuple[str, ...]:
        # Notion 的权限模型是 capabilities + 页面级授权,没有 OAuth scope 字符串。
        return ()

    # ---- 授权 ----

    def authorize_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        # Notion 不支持 PKCE:code_challenge 由平台统一生成,这里忽略。
        del code_challenge
        query = urlencode(
            {
                "client_id": self._client_id,
                "response_type": "code",
                "owner": "user",
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"{self._api}{_AUTHORIZE_PATH}?{query}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client: httpx.AsyncClient,
    ) -> Credentials:
        # Notion 换 token 不接受 code_verifier(无 PKCE),由平台统一传入后忽略。
        del code_verifier
        payload = await self._token_request(
            client,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        self._token_meta = _token_meta(payload)
        return self._credentials_from_token(payload)

    async def refresh(
        self, credentials: Credentials, client: httpx.AsyncClient
    ) -> Credentials:
        if not credentials.refresh_token:
            raise ProviderError(
                "connector.auth_expired",
                "Notion 连接没有 refresh token,需要重新授权",
                hint="请重新连接 Notion",
            )
        payload = await self._token_request(
            client,
            {
                "grant_type": "refresh_token",
                "refresh_token": credentials.refresh_token,
            },
        )
        meta = _token_meta(payload)
        if meta.get("workspace_id") or meta.get("workspace_name"):
            self._token_meta = meta
        return self._credentials_from_token(
            payload, fallback_refresh=credentials.refresh_token
        )

    def _basic_auth(self) -> str:
        raw = f"{self._client_id}:{self._client_secret}".encode()
        return base64.b64encode(raw).decode("ascii")

    async def _token_request(
        self, client: httpx.AsyncClient, body: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            response = await client.post(
                f"{self._api}{_TOKEN_PATH}",
                json=body,
                headers={
                    "Authorization": f"Basic {self._basic_auth()}",
                    "Notion-Version": self._version,
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "connector.upstream_timeout",
                "Notion 授权服务超时",
                hint="稍后重试",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "connector.upstream_unavailable",
                "Notion 授权服务不可达",
                hint="稍后重试",
                retryable=True,
            ) from exc
        payload = _json_or_none(response)
        if response.status_code >= 400 or _has_error(payload):
            raise _token_error(response, payload)
        return payload or {}

    def _credentials_from_token(
        self, payload: dict[str, Any], *, fallback_refresh: str | None = None
    ) -> Credentials:
        access_token = payload.get("access_token")
        if not access_token:
            raise ProviderError("connector.oauth_failed", "Notion 未返回 access_token")
        return Credentials(
            access_token=str(access_token),
            refresh_token=_opt_str(payload.get("refresh_token")) or fallback_refresh,
            token_type=str(payload.get("token_type") or "Bearer"),
            scope="",
            # Notion 不返回 expires_in(响应 schema 里没有),TTL 未知:
            # 不做按时间惰性刷新,401 时由 service 层置 expired。
            expires_at=None,
            refresh_expires_at=None,
        )

    # ---- 身份 ----

    async def fetch_identity(
        self, credentials: Credentials, client: httpx.AsyncClient
    ) -> Identity:
        data = await self._request(
            client, "GET", f"{self._api}/v1/users/me", credentials
        )
        bot = data.get("bot") or {}
        user_name = _opt_str(data.get("name"))
        workspace_name = _opt_str(bot.get("workspace_name")) or _opt_str(
            self._token_meta.get("workspace_name")
        )
        workspace_id = _opt_str(self._token_meta.get("workspace_id"))
        owner = bot.get("owner") or {}
        owner_type = _opt_str(owner.get("type")) or _opt_str(
            self._token_meta.get("owner_type")
        )
        owner_subject = (
            _owner_subject(owner)
            or _opt_str(self._token_meta.get("owner_subject"))
            or "workspace"
        )
        bot_id = _opt_str(data.get("id"))
        # 唯一键:一个 workspace_id + 一个授权 owner(account = "<workspace>:<owner>")。
        account = (
            f"{workspace_id}:{owner_subject}"
            if workspace_id
            else f"{workspace_name or bot_id or 'notion'}:{owner_subject}"
        )
        return Identity(
            account=account,
            display_name=user_name or workspace_name or "Notion",
            provider_account_id=bot_id,
            external_workspace=workspace_name or workspace_id,
            metadata={
                "workspace_id": workspace_id,
                "workspace_name": workspace_name,
                "bot_id": bot_id,
                "owner_type": owner_type,
                "owner_subject": owner_subject,
                "user_name": user_name,
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

    async def _call_search(
        self,
        arguments: dict[str, Any],
        creds: Credentials,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"page_size": _page_size(arguments)}
        query = _opt_str(arguments.get("query"))
        if query:
            body["query"] = query
        object_type = _opt_str(arguments.get("object_type"))
        if object_type in ("page", "data_source"):
            body["filter"] = {"property": "object", "value": object_type}
        cursor = _opt_str(arguments.get("start_cursor"))
        if cursor:
            body["start_cursor"] = cursor
        data = await self._request(
            client, "POST", f"{self._api}/v1/search", creds, body=body
        )
        return {
            "results": [_summary(item) for item in _results(data)],
            "has_more": bool(data.get("has_more")),
            "next_cursor": data.get("next_cursor"),
        }

    async def _call_read_page(
        self,
        arguments: dict[str, Any],
        creds: Credentials,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        page_id = _require_str(arguments, "page_id")
        data = await self._request(
            client,
            "GET",
            f"{self._api}/v1/pages/{quote(page_id, safe='')}",
            creds,
        )
        return {
            "id": data.get("id"),
            "url": data.get("url"),
            "created_time": data.get("created_time"),
            "last_edited_time": data.get("last_edited_time"),
            "archived": data.get("archived"),
            "in_trash": data.get("in_trash"),
            "parent": data.get("parent"),
            "properties": data.get("properties"),
        }

    async def _call_read_blocks(
        self,
        arguments: dict[str, Any],
        creds: Credentials,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        block_id = _require_str(arguments, "block_id")
        data = await self._request(
            client,
            "GET",
            f"{self._api}/v1/blocks/{quote(block_id, safe='')}/children",
            creds,
            params=_cursor_params(arguments),
        )
        return {
            "results": _results(data),
            "has_more": bool(data.get("has_more")),
            "next_cursor": data.get("next_cursor"),
        }

    async def _call_query_data_source(
        self,
        arguments: dict[str, Any],
        creds: Credentials,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        data_source_id = _require_str(arguments, "data_source_id")
        body: dict[str, Any] = {"page_size": _page_size(arguments)}
        filters = arguments.get("filter")
        if isinstance(filters, dict):
            body["filter"] = filters
        sorts = arguments.get("sorts")
        if isinstance(sorts, list):
            body["sorts"] = sorts
        cursor = _opt_str(arguments.get("start_cursor"))
        if cursor:
            body["start_cursor"] = cursor
        data = await self._request(
            client,
            "POST",
            f"{self._api}/v1/data_sources/{quote(data_source_id, safe='')}/query",
            creds,
            body=body,
        )
        return {
            "results": _results(data),
            "has_more": bool(data.get("has_more")),
            "next_cursor": data.get("next_cursor"),
        }

    async def _call_create_page(
        self,
        arguments: dict[str, Any],
        creds: Credentials,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        parent_id = _require_str(arguments, "parent_id")
        title = _require_str(arguments, "title")
        parent_type = _opt_str(arguments.get("parent_type")) or "page"
        if parent_type not in ("page", "data_source"):
            raise ProviderError(
                "connector.invalid_arguments",
                "parent_type 只支持 page 或 data_source",
            )
        title_property = _opt_str(arguments.get("title_property")) or "title"
        body: dict[str, Any] = {
            # 2026-03-11 版接口的父级键是 <type>_id(页面/数据源)
            "parent": {f"{parent_type}_id": parent_id},
            "properties": {title_property: {"title": [_rich_text(title)]}},
        }
        children = _paragraph_blocks(arguments.get("paragraphs"))
        if children:
            body["children"] = children
        data = await self._request(
            client, "POST", f"{self._api}/v1/pages", creds, body=body
        )
        return {
            "id": data.get("id"),
            "url": data.get("url"),
            "created_time": data.get("created_time"),
            "last_edited_time": data.get("last_edited_time"),
            "parent": data.get("parent"),
        }

    async def _call_append_blocks(
        self,
        arguments: dict[str, Any],
        creds: Credentials,
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        block_id = _require_str(arguments, "block_id")
        children = _paragraph_blocks(arguments.get("paragraphs"), required=True)
        body: dict[str, Any] = {"children": children}
        after = _opt_str(arguments.get("after"))
        if after:
            body["after"] = after
        data = await self._request(
            client,
            "PATCH",
            f"{self._api}/v1/blocks/{quote(block_id, safe='')}/children",
            creds,
            body=body,
        )
        return {
            "results": [_block_summary(item) for item in _results(data)],
            "has_more": bool(data.get("has_more")),
        }

    # ---- HTTP ----

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        creds: Credentials,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await client.request(
                method,
                url,
                params=params,
                json=body,
                headers={
                    "Authorization": f"Bearer {creds.access_token}",
                    "Notion-Version": self._version,
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "connector.upstream_timeout",
                "Notion 接口超时",
                hint="稍后重试",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "connector.upstream_unavailable",
                "Notion 接口不可达",
                hint="稍后重试",
                retryable=True,
            ) from exc
        payload = _json_or_none(response)
        if response.status_code >= 400 or _has_error(payload):
            raise _error_from_response(response, payload)
        return payload or {}


def _token_meta(payload: dict[str, Any]) -> dict[str, Any]:
    owner = payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
    return {
        "workspace_id": _opt_str(payload.get("workspace_id")),
        "workspace_name": _opt_str(payload.get("workspace_name")),
        "bot_id": _opt_str(payload.get("bot_id")),
        "owner_type": _opt_str(owner.get("type")),
        "owner_subject": _owner_subject(owner),
    }


def _owner_subject(owner: dict[str, Any]) -> str | None:
    if not isinstance(owner, dict):
        return None
    if owner.get("type") == "user":
        user = owner.get("user") if isinstance(owner.get("user"), dict) else {}
        return _opt_str(user.get("id")) or _opt_str(
            (user.get("person") or {}).get("email")
        )
    if owner.get("type") == "workspace":
        return "workspace"
    return None


def _page_size(arguments: dict[str, Any]) -> int:
    raw = arguments.get("page_size", _PAGE_SIZE_DEFAULT)
    try:
        page_size = int(raw)
    except (TypeError, ValueError):
        page_size = _PAGE_SIZE_DEFAULT
    return max(1, min(_PAGE_SIZE_MAX, page_size))


def _cursor_params(arguments: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {"page_size": _page_size(arguments)}
    cursor = _opt_str(arguments.get("start_cursor"))
    if cursor:
        params["start_cursor"] = cursor
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


def _results(data: dict[str, Any]) -> list[dict[str, Any]]:
    results = data.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "object": item.get("object"),
        "url": item.get("url"),
        "last_edited_time": item.get("last_edited_time"),
        "title": _title_of(item),
    }


def _title_of(item: dict[str, Any]) -> str | None:
    """从 page/data source 对象里抽标题纯文本;不解析 url/href/cursor。"""
    title = item.get("title")
    if isinstance(title, list):
        text = _plain_text(title)
        if text:
            return text
    properties = item.get("properties")
    if isinstance(properties, dict):
        for prop in properties.values():
            if not isinstance(prop, dict) or prop.get("type") != "title":
                continue
            text = _plain_text(prop.get("title"))
            if text:
                return text
    return None


def _plain_text(rich_text: Any) -> str | None:
    if not isinstance(rich_text, list):
        return None
    parts = [
        str(item.get("plain_text") or "")
        for item in rich_text
        if isinstance(item, dict)
    ]
    joined = "".join(part for part in parts if part)
    return joined or None


def _rich_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": text}}


def _paragraph_blocks(value: Any, *, required: bool = False) -> list[dict[str, Any]]:
    """把纯文本段落转成 Notion 的 paragraph 块(写入接口只收块结构)。"""
    if not value:
        if required:
            raise ProviderError(
                "connector.invalid_arguments", "缺少必填参数: paragraphs"
            )
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ProviderError(
            "connector.invalid_arguments", "paragraphs 必须是非空字符串数组"
        )
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [_rich_text(item.strip())]},
        }
        for item in value
    ]


def _block_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item.get("id"), "type": item.get("type")}


def _json_or_none(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _has_error(payload: dict[str, Any] | None) -> bool:
    return isinstance(payload, dict) and payload.get("object") == "error"


def _error_code(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    return _opt_str(payload.get("code"))


def _retry_after(
    response: httpx.Response, payload: dict[str, Any] | None
) -> int | None:
    raw = response.headers.get("Retry-After") or (
        payload.get("retry_after") if isinstance(payload, dict) else None
    )
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _token_error(
    response: httpx.Response, payload: dict[str, Any] | None
) -> ProviderError:
    """换/刷 token 的错误分层:限流与上游故障不能被当成"需要重新授权"。"""
    status = response.status_code
    code = _error_code(payload)
    label = code or status
    if status == 429:
        retry_after = _retry_after(response, payload)
        hint = (
            f"Notion 授权服务限流,建议 {retry_after} 秒后重试"
            if retry_after
            else "Notion 授权服务限流,请稍后重试"
        )
        return ProviderError(
            "connector.upstream_rate_limited",
            f"Notion 授权服务限流(代码 {label})",
            hint=hint,
            retryable=True,
        )
    if status >= 500:
        return ProviderError(
            "connector.upstream_unavailable",
            f"Notion 授权服务暂不可用(HTTP {status})",
            hint="稍后重试",
            retryable=True,
        )
    if status in (400, 401, 403):
        return ProviderError(
            "connector.auth_expired",
            f"Notion 授权失败(代码 {label})",
            hint="请重新连接 Notion 或检查应用凭证",
        )
    return ProviderError(
        "connector.oauth_failed",
        f"Notion 授权失败(代码 {label})",
        hint="请重新发起授权",
    )


def _error_from_response(
    response: httpx.Response, payload: dict[str, Any] | None
) -> ProviderError:
    status = response.status_code
    code = _error_code(payload)
    label = code or status
    if status == 429:
        retry_after = _retry_after(response, payload)
        hint = (
            f"Notion 限流,建议 {retry_after} 秒后重试"
            if retry_after
            else "Notion 限流,请稍后重试"
        )
        return ProviderError(
            "connector.upstream_rate_limited",
            f"Notion 接口限流(代码 {label})",
            hint=hint,
            retryable=True,
        )
    if status == 401:
        return ProviderError(
            "connector.auth_expired",
            f"Notion 授权已失效(代码 {label})",
            hint="请重新连接 Notion",
        )
    if status == 403:
        return ProviderError(
            "connector.scope_missing",
            f"Notion 授权缺少所需能力(代码 {label})",
            hint="请在 Notion 里把页面共享给该集成,或重新授权",
        )
    if status == 400:
        return ProviderError(
            "connector.invalid_arguments",
            f"Notion 拒绝请求参数(代码 {label})",
            hint="检查参数后重试",
        )
    if status == 404:
        return ProviderError(
            "connector.upstream_error",
            f"Notion 未找到对象(代码 {label})",
            hint="检查 id 是否正确,或页面是否已授权给集成",
        )
    if status >= 500:
        return ProviderError(
            "connector.upstream_unavailable",
            f"Notion 接口暂不可用(HTTP {status})",
            hint="稍后重试",
            retryable=True,
        )
    return ProviderError(
        "connector.upstream_error",
        f"Notion 接口返回错误(代码 {label})",
        hint="检查参数或稍后重试",
    )
