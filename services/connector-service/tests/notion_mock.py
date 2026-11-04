"""Notion 上游桩:OAuth、身份与只读工具接口(全部 MockTransport,零网络)。

Notion 的关键差异(相对于一般 OAuth provider):换 token 用 HTTP Basic Auth、
授权页带 `owner=user`、token 响应不含 `expires_in`、每个 API 请求都要
`Notion-Version` 头,workspace 信息只能从换 token 响应里拿到。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

TOKEN_PAYLOAD: dict[str, Any] = {
    "access_token": "notion-at-1",
    "token_type": "bearer",
    "refresh_token": "notion-rt-1",
    "bot_id": "bot-1",
    "workspace_name": "Acme Workspace",
    "workspace_icon": "https://x/icon.png",
    "workspace_id": "ws-1",
    "owner": {
        "type": "user",
        "user": {"id": "owner-1", "object": "user", "name": "张三"},
    },
}

SEARCH_RESULTS: list[dict[str, Any]] = [
    {
        "object": "page",
        "id": "page-1",
        "url": "https://www.notion.so/page-1",
        "last_edited_time": "2026-11-04T08:00:00.000Z",
        "properties": {
            "title": {
                "type": "title",
                "title": [{"plain_text": "项目计划", "type": "text"}],
            }
        },
    },
    {
        "object": "data_source",
        "id": "ds-1",
        "url": "https://www.notion.so/ds-1",
        "last_edited_time": "2026-11-04T09:00:00.000Z",
        "title": [{"plain_text": "任务表", "type": "text"}],
    },
]

PAGE_OBJECT: dict[str, Any] = {
    "object": "page",
    "id": "page-1",
    "url": "https://www.notion.so/page-1",
    "created_time": "2026-11-01T00:00:00.000Z",
    "last_edited_time": "2026-11-04T08:00:00.000Z",
    "archived": False,
    "in_trash": False,
    "parent": {"type": "workspace", "workspace": True},
    "properties": {
        "title": {"type": "title", "title": [{"plain_text": "项目计划"}]}
    },
}

BLOCKS: list[dict[str, Any]] = [
    {
        "object": "block",
        "id": "block-1",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": "第一段", "type": "text"}]},
    }
]

QUERY_ROWS: list[dict[str, Any]] = [
    {
        "object": "page",
        "id": "row-1",
        "properties": {"名称": {"type": "title", "title": [{"plain_text": "写周报"}]}},
    }
]


class NotionMock:
    """可断言的 Notion 上游:记录换 token 的 Basic Auth/body 与 API 请求头。"""

    def __init__(self) -> None:
        self.token_calls: list[dict[str, Any]] = []
        self.token_auth: list[str | None] = []
        self.api_calls: list[tuple[str, str, str | None, str | None]] = []
        self.fail: dict[str, httpx.Response] = {}

    # ---- httpx 处理器 ----

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for marker, response in self.fail.items():
            if marker in path:
                return response
        if path.endswith("/oauth/token"):
            return self._token(request)
        self.api_calls.append(
            (
                request.method,
                str(request.url),
                request.headers.get("authorization"),
                request.headers.get("notion-version"),
            )
        )
        if path.endswith("/users/me"):
            return _json(_user_object())
        if path.endswith("/search"):
            return _json(_list(SEARCH_RESULTS))
        if path.endswith("/query"):
            return _json(_list(QUERY_ROWS))
        if path.endswith("/children"):
            return _json(_list(BLOCKS))
        if path.startswith("/v1/pages/"):
            return _json(PAGE_OBJECT)
        return _json({"object": "error", "code": "object_not_found"}, status=404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.token_auth.append(request.headers.get("authorization"))
        self.token_calls.append(json.loads(request.content or b"{}"))
        return _json(dict(TOKEN_PAYLOAD))


def _user_object() -> dict[str, Any]:
    return {
        "object": "user",
        "id": "bot-1",
        "name": "张三",
        "type": "bot",
        "bot": {
            "owner": {
                "type": "user",
                "user": {"id": "owner-1", "object": "user", "name": "张三"},
            },
            "workspace_name": "Acme Workspace",
        },
    }


def _list(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "list",
        "results": results,
        "has_more": False,
        "next_cursor": None,
    }


def _json(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def error_response(
    status: int,
    code: str = "invalid_request_url",
    *,
    retry_after: str | None = None,
) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after else {}
    return httpx.Response(
        status,
        json={"object": "error", "status": status, "code": code},
        headers=headers,
    )
