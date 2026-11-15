"""web_search 执行器:提供方请求/信封、结果整形与参数上限(零网络)。"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.web_search_support import (
    json_handler,
    respond,
    search_client,
    tavily_payload,
)
from tool_service.services.executors.web_search import (
    EMPTY_RESULTS_NOTE,
    SNIPPET_TRUNCATED_NOTE,
    TITLE_TRUNCATED_NOTE,
    SearchClient,
)


async def test_tavily_request_and_envelope() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=tavily_payload())

    client = search_client(handler)
    try:
        result = await client.search("beauty moon", max_results=2)
    finally:
        await client.aclose()

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["body"] == {
        "api_key": "test-key",
        "query": "beauty moon",
        "max_results": 2,
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
    }
    assert result["query"] == "beauty moon"
    assert result["provider"] == "tavily"
    assert result["results"] == [
        {
            "rank": 1,
            "title": "Beauty Moon",
            "url": "https://example.test/a",
            "snippet": "first hit",
        },
        {
            "rank": 2,
            "title": "Moon Docs",
            "url": "https://example.test/b",
            "snippet": "second hit",
        },
    ]
    assert "notes" not in result


async def test_brave_request_and_envelope() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["token"] = request.headers.get("X-Subscription-Token")
        captured["accept"] = request.headers.get("Accept")
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Brave Hit",
                            "url": "https://example.test/brave",
                            "description": "brave snippet",
                        }
                    ]
                }
            },
        )

    client = search_client(handler, provider="brave")
    try:
        result = await client.search("beauty moon", max_results=3)
    finally:
        await client.aclose()

    assert captured["method"] == "GET"
    assert captured["path"] == "/res/v1/web/search"
    assert captured["params"] == {"q": "beauty moon", "count": "3"}
    assert captured["token"] == "test-key"
    assert captured["accept"] == "application/json"
    assert result["provider"] == "brave"
    assert result["results"][0]["snippet"] == "brave snippet"
    assert result["results"][0]["rank"] == 1


async def test_omitted_max_results_uses_default() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": []})

    client = search_client(handler)
    try:
        await client.search("q")
    finally:
        await client.aclose()

    assert captured["body"]["max_results"] == 5


async def test_max_results_is_clamped_to_configured_limit() -> None:
    captured: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content)["max_results"])
        return httpx.Response(200, json={"results": []})

    client = search_client(handler, max_results=10)
    try:
        await client.search("q", max_results=99)
    finally:
        await client.aclose()

    assert captured == [10]


async def test_default_results_is_clamped_to_max() -> None:
    captured: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content)["max_results"])
        return httpx.Response(200, json={"results": []})

    client = search_client(handler, default_results=50, max_results=5)
    try:
        await client.search("q")
    finally:
        await client.aclose()

    assert captured == [5]


async def test_long_title_and_snippet_are_truncated_with_notes() -> None:
    payload = {
        "results": [
            {
                "title": "t" * 400,
                "url": "https://example.test/a",
                "content": "s" * 500,
            }
        ]
    }
    client = search_client(json_handler(payload))
    try:
        result = await client.search("q")
    finally:
        await client.aclose()

    hit = result["results"][0]
    assert len(hit["title"]) == 300
    assert hit["title"].endswith("…")
    assert len(hit["snippet"]) == 400
    assert hit["snippet"].endswith("…")
    assert result["notes"] == [TITLE_TRUNCATED_NOTE, SNIPPET_TRUNCATED_NOTE]


async def test_empty_results_is_not_an_error() -> None:
    client = search_client(json_handler({"results": []}))
    try:
        result = await client.search("q")
    finally:
        await client.aclose()

    assert result["results"] == []
    assert result["notes"] == [EMPTY_RESULTS_NOTE]


async def test_invalid_urls_are_dropped_and_ranks_stay_contiguous() -> None:
    payload = {
        "results": [
            {"title": "no url", "url": "", "content": "x"},
            {"title": "too long", "url": "https://example.test/" + "x" * 3000},
            {"title": "ok", "url": "https://example.test/ok", "content": "y"},
        ]
    }
    client = search_client(json_handler(payload))
    try:
        result = await client.search("q")
    finally:
        await client.aclose()

    assert [hit["url"] for hit in result["results"]] == ["https://example.test/ok"]
    assert result["results"][0]["rank"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": ""},
        {"provider": "google"},
        {"base_url": "http://api.tavily.com"},
        {"base_url": "https://"},
    ],
)
def test_constructor_rejects_invalid_configuration(kwargs: dict[str, Any]) -> None:
    merged = {"api_key": "test-key", **kwargs}
    with pytest.raises(ValueError):
        SearchClient(transport=httpx.MockTransport(respond(200)), **merged)


async def test_custom_https_base_url_is_used() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["path"] = request.url.path
        return httpx.Response(200, json={"results": []})

    client = search_client(handler, base_url="https://search.example.test/")
    try:
        await client.search("q")
    finally:
        await client.aclose()

    assert captured == {"host": "search.example.test", "path": "/search"}
