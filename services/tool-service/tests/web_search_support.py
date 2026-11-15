"""web_search 测试共享装配:MockTransport 客户端工厂与固定载荷/解析器。"""

from __future__ import annotations

from typing import Any

import httpx

from tool_service.services.executors.web_search import SearchClient

PUBLIC_IP = "93.184.216.34"


def resolver(mapping: dict[str, list[str]]):
    """按主机名返回固定地址;不在表里的主机按不可解析处理。"""

    async def resolve(host: str, port: int) -> list[str]:
        return mapping.get(host, [])

    return resolve


async def public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def search_client(
    handler: Any,
    *,
    provider: str = "tavily",
    resolver: Any = public_resolver,
    base_url: str = "",
    **kwargs: Any,
) -> SearchClient:
    """构造注入了 MockTransport 与假解析器的搜索客户端。"""

    return SearchClient(
        provider=provider,
        api_key="test-key",
        base_url=base_url,
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        **kwargs,
    )


def json_handler(payload: dict[str, Any], status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def respond(status: int, *, body: bytes = b""):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return handler


def raise_error(error: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def tavily_payload() -> dict[str, Any]:
    return {
        "results": [
            {
                "title": "Beauty Moon",
                "url": "https://example.test/a",
                "content": "first hit",
                "score": 0.9,
            },
            {
                "title": "Moon Docs",
                "url": "https://example.test/b",
                "content": "second hit",
                "score": 0.5,
            },
        ]
    }
