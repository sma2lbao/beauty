"""demo provider 的工具清单、入参解析与出参整形(测试专用假上游)。"""

from __future__ import annotations

from typing import Any

import httpx

from connector.providers.base import Credentials, ToolSpec
from connector.providers.errors import ProviderError

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 50

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_me",
        title="我的账号身份",
        description="返回当前授权用户的账号 id、姓名与工作区标识。",
        input_schema={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="list_files",
        title="列出文件",
        description="列出云空间(我的空间或指定文件夹)下的文件与文件夹。",
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
        description="按文档 token 读取文档的纯文本正文(raw_content)。",
        input_schema={
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "文档 token(document_id)",
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
        description="列出当前用户可见的知识库(spaces)。",
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


class _ToolsMixin:
    """按工具名分派到对应 `_call_*` 处理器。"""

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
            "account_id": identity.provider_account_id,
            "name": identity.display_name,
            "workspace_id": identity.external_workspace,
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
        data = await self._get(client, f"{self.API_BASE}/files", creds, params=params)
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
            f"{self.API_BASE}/documents/{document_id}/raw_content",
            creds,
        )
        return {"document_id": document_id, "content": data.get("content", "")}

    async def _call_list_document_blocks(
        self, arguments: dict[str, Any], creds: Credentials, client: httpx.AsyncClient
    ) -> dict[str, Any]:
        document_id = _require_str(arguments, "document_id")
        data = await self._get(
            client,
            f"{self.API_BASE}/documents/{document_id}/blocks",
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
            f"{self.API_BASE}/spaces",
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
            f"{self.API_BASE}/spaces/{space_id}/nodes",
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
