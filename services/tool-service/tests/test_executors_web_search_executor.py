"""web_search 执行器分派:缺客户端、成功信封、错误映射与结果过大保险丝。"""

from __future__ import annotations

from typing import Any

from tests.web_search_support import (
    json_handler,
    respond,
    search_client,
    tavily_payload,
)
from tool_service.services.executors.http import HttpToolExecutor


async def test_executor_reports_missing_client() -> None:
    response = await HttpToolExecutor().execute("t-1", "web_search", {"query": "q"})

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.executor_unavailable"
    assert "TOOL__SEARCH_ENABLED" in response.error.hint


async def test_executor_returns_success_envelope() -> None:
    client = search_client(json_handler(tavily_payload()))
    try:
        response = await HttpToolExecutor(searcher=client).execute(
            "t-1", "web_search", {"query": "q", "max_results": 1}
        )
    finally:
        await client.aclose()

    assert response.ok is True
    assert response.result["provider"] == "tavily"
    assert response.result["results"][0]["rank"] == 1


async def test_executor_maps_search_error() -> None:
    client = search_client(respond(429))
    try:
        response = await HttpToolExecutor(searcher=client).execute(
            "t-1", "web_search", {"query": "q"}
        )
    finally:
        await client.aclose()

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.upstream_rate_limited"
    assert response.error.retryable is True


class _HugeSearcher:
    """结果远超单条工具上限的替身(验证 too_large 保险丝)。"""

    async def search(
        self, query: str, *, max_results: int | None = None
    ) -> dict[str, Any]:
        return {
            "query": query,
            "provider": "tavily",
            "results": [
                {
                    "rank": 1,
                    "title": "t" * 25_000,
                    "url": "https://example.test/a",
                    "snippet": "",
                }
            ],
        }


async def test_executor_rejects_oversized_result() -> None:
    response = await HttpToolExecutor(searcher=_HugeSearcher()).execute(
        "t-1", "web_search", {"query": "q"}
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.too_large"
    assert response.error.retryable is True
