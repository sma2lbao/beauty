"""GitHub 11 个工具的请求参数/结果整形/错误映射(全 MockTransport)。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from connector.core.config import Settings
from connector.providers.base import Credentials, ToolRisk
from connector.providers.errors import ProviderError
from connector.providers.github import GithubProvider
from connector.providers.github_api import CONTENT_LIMIT, truncate
from connector.providers.github_tools import TOOLS
from tests.github_mock import GithubMock, rate_limit_response

CREDS = Credentials(access_token="ghu_access_1")


def _provider() -> GithubProvider:
    return GithubProvider(
        Settings(
            _env_file=None,
            env="dev",
            github_client_id="cid",
            github_client_secret="secret",
        )
    )


async def _call(
    mock: GithubMock,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock.handler)) as client:
        return await _provider().call(tool, arguments, CREDS, client)


def _call_get_repo(mock: GithubMock) -> Any:
    return _call(mock, "Github__get_repository", {"repo": "octo/hello"})


def test_tool_catalog_has_eleven_tools_and_three_writes() -> None:
    assert len(TOOLS) == 11
    writes = {spec.name for spec in TOOLS if spec.risk is ToolRisk.WRITE}
    assert writes == {
        "create_issue",
        "create_issue_comment",
        "create_pull_request",
    }
    assert {spec.name for spec in TOOLS} == {
        "list_repositories",
        "get_repository",
        "get_file_contents",
        "search_code",
        "list_issues",
        "get_issue",
        "list_pull_requests",
        "get_pull_request",
        "create_issue",
        "create_issue_comment",
        "create_pull_request",
    }
    for spec in TOOLS:
        assert spec.input_schema["type"] == "object"
        assert spec.input_schema["additionalProperties"] is False


async def test_list_repositories_passes_filters_and_clamps_per_page() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__list_repositories",
        {
            "visibility": "private",
            "affiliation": "owner",
            "sort": "updated",
            "direction": "desc",
            "page": 2,
            "per_page": 500,
        },
    )
    repository = result["repositories"][0]
    assert repository["full_name"] == "octo/hello"
    assert repository["owner"] == "octo"
    assert "license" not in repository  # 列表摘要不返回 license
    query = mock.api_calls[0]["query"]
    assert query["per_page"] == "100"
    assert query["page"] == "2"
    assert query["visibility"] == "private"
    assert query["affiliation"] == "owner"
    assert query["sort"] == "updated"
    assert query["direction"] == "desc"


@pytest.mark.parametrize(
    "arguments",
    [
        {"visibility": "team"},
        {"sort": "stars"},
        {"direction": "up"},
        {"page": 0},
        {"per_page": "many"},
        {"page": True},
    ],
)
async def test_list_repositories_rejects_bad_filters(
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ProviderError) as raised:
        await _call(GithubMock(), "Github__list_repositories", arguments)
    assert raised.value.code == "connector.invalid_arguments"


async def test_get_repository_reads_optional_readme() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__get_repository",
        {"repo": "octo/hello", "include_readme": True},
    )
    assert result["repository"]["topics"] == ["demo"]
    assert result["repository"]["license"] == "MIT"
    assert result["readme"]["content"] == "# Hello"
    assert result["readme"]["truncated"] is False
    assert [call["path"] for call in mock.api_calls] == [
        "/repos/octo/hello",
        "/repos/octo/hello/readme",
    ]


async def test_get_repository_returns_null_when_readme_missing() -> None:
    mock = GithubMock()
    mock.fail["/repos/octo/hello/readme"] = httpx.Response(
        404, json={"message": "Not Found"}
    )
    result = await _call(
        mock,
        "Github__get_repository",
        {"repo": "octo/hello", "include_readme": True},
    )
    assert result["readme"] is None
    assert result["repository"]["full_name"] == "octo/hello"


@pytest.mark.parametrize(
    "repo",
    ["octo", "octo/hello/extra", "-octo/hello", "octo/hel lo", ""],
)
async def test_invalid_repo_argument_is_rejected(repo: str) -> None:
    with pytest.raises(ProviderError) as raised:
        await _call(GithubMock(), "Github__get_repository", {"repo": repo})
    assert raised.value.code == "connector.invalid_arguments"


async def test_get_file_contents_directory() -> None:
    mock = GithubMock()
    result = await _call(
        mock, "Github__get_file_contents", {"repo": "octo/hello", "path": ""}
    )
    assert result["type"] == "dir"
    assert [entry["path"] for entry in result["entries"]] == [
        "README.md",
        "src",
    ]
    assert mock.api_calls[0]["path"] == "/repos/octo/hello/contents/"


async def test_get_file_contents_file_decodes_base64_and_ref() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__get_file_contents",
        {"repo": "octo/hello", "path": "README.md", "ref": "feature"},
    )
    assert result["type"] == "file"
    assert result["content"] == "# Hello"
    assert result["truncated"] is False
    assert result["note"] is None
    assert mock.api_calls[0]["query"]["ref"] == "feature"


async def test_get_file_contents_non_base64_large_file() -> None:
    result = await _call(
        GithubMock(),
        "Github__get_file_contents",
        {"repo": "octo/hello", "path": "big.bin"},
    )
    assert result["content"] == ""
    assert result["truncated"] is True
    assert "1MB" in (result["note"] or "")


def test_truncate_limits_utf8_bytes() -> None:
    text = "汉" * (CONTENT_LIMIT // 3 + 2)
    clipped, truncated = truncate(text, CONTENT_LIMIT)
    assert truncated is True
    assert len(clipped.encode("utf-8")) <= CONTENT_LIMIT
    assert clipped == text[: len(clipped)]


async def test_get_file_contents_truncates_large_text() -> None:
    result = await _call(
        GithubMock(),
        "Github__get_file_contents",
        {"repo": "octo/hello", "path": "huge.txt"},
    )
    assert len(result["content"]) == CONTENT_LIMIT
    assert result["truncated"] is True
    assert "256KB" in (result["note"] or "")


@pytest.mark.parametrize(
    "arguments",
    [
        {"repo": "octo/hello"},
        {"repo": "octo/hello", "path": 1},
        {"repo": "octo/hello", "path": "../secret"},
        {"repo": "octo/hello", "path": "a/../../b"},
    ],
)
async def test_get_file_contents_rejects_bad_path(
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ProviderError) as raised:
        await _call(GithubMock(), "Github__get_file_contents", arguments)
    assert raised.value.code == "connector.invalid_arguments"


async def test_search_code_maps_items_and_paging() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__search_code",
        {"q": "hello", "page": 1, "per_page": 10},
    )
    assert result["total_count"] == 1
    assert result["incomplete_results"] is False
    assert result["items"][0]["repository"] == "octo/hello"
    assert mock.api_calls[0]["query"]["q"] == "hello"


@pytest.mark.parametrize("query", ["", "x" * 257])
async def test_search_code_rejects_bad_query(query: str) -> None:
    with pytest.raises(ProviderError) as raised:
        await _call(GithubMock(), "Github__search_code", {"q": query})
    assert raised.value.code == "connector.invalid_arguments"


async def test_list_issues_filters_pull_requests() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__list_issues",
        {"repo": "octo/hello", "state": "all", "per_page": 100},
    )
    assert [issue["number"] for issue in result["issues"]] == [7]
    assert result["issues"][0]["labels"] == ["bug"]
    assert mock.api_calls[0]["query"]["state"] == "all"


async def test_list_issues_rejects_bad_state() -> None:
    with pytest.raises(ProviderError) as raised:
        await _call(
            GithubMock(),
            "Github__list_issues",
            {"repo": "octo/hello", "state": "merged"},
        )
    assert raised.value.code == "connector.invalid_arguments"


async def test_get_issue_reads_comments_and_marks_truncation() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__get_issue",
        {"repo": "octo/hello", "number": 7, "comment_limit": 1},
    )
    assert result["issue"]["body"] == "body"
    assert len(result["comments"]) == 2
    assert result["comments"][0]["body"] == "first"
    assert result["comments_truncated"] is True
    paths = [call["path"] for call in mock.api_calls]
    assert paths == [
        "/repos/octo/hello/issues/7",
        "/repos/octo/hello/issues/7/comments",
    ]
    assert mock.api_calls[1]["query"]["per_page"] == "1"


@pytest.mark.parametrize(
    "arguments",
    [
        {"repo": "octo/hello"},
        {"repo": "octo/hello", "number": 0},
        {"repo": "octo/hello", "number": "x"},
        {"repo": "octo/hello", "number": 7, "comment_limit": 101},
        {"repo": "octo/hello", "number": 7, "comment_limit": 0},
    ],
)
async def test_get_issue_rejects_bad_arguments(
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ProviderError) as raised:
        await _call(GithubMock(), "Github__get_issue", arguments)
    assert raised.value.code == "connector.invalid_arguments"


async def test_list_pull_requests_maps_summary() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__list_pull_requests",
        {"repo": "octo/hello", "state": "closed"},
    )
    pull = result["pull_requests"][0]
    assert pull["number"] == 3
    assert pull["head"] == {"ref": "feature", "sha": "abc123"}
    assert pull["base"] == {"ref": "main"}
    assert mock.api_calls[0]["query"]["state"] == "closed"


async def test_get_pull_request_without_diff() -> None:
    result = await _call(
        GithubMock(),
        "Github__get_pull_request",
        {"repo": "octo/hello", "number": 3},
    )
    assert result["pull_request"]["merged"] is False
    assert result["diff"] is None
    assert result["diff_truncated"] is False


async def test_get_pull_request_with_diff_uses_diff_media_type() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__get_pull_request",
        {"repo": "octo/hello", "number": 3, "include_diff": True},
    )
    assert result["diff"] == mock.diff_text
    assert result["diff_truncated"] is False
    diff_calls = [
        call for call in mock.api_calls if call["path"] == "/repos/octo/hello/pulls/3"
    ]
    assert diff_calls[-1]["accept"] == "application/vnd.github.diff"


async def test_get_pull_request_truncates_huge_diff() -> None:
    mock = GithubMock()
    mock.diff_text = "d" * (CONTENT_LIMIT + 5)
    result = await _call(
        mock,
        "Github__get_pull_request",
        {"repo": "octo/hello", "number": 3, "include_diff": True},
    )
    assert len(result["diff"]) == CONTENT_LIMIT
    assert result["diff_truncated"] is True


async def test_create_issue_posts_title_body_and_labels() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__create_issue",
        {
            "repo": "octo/hello",
            "title": "Bug",
            "body": "details",
            "labels": ["bug", "p1"],
        },
    )
    assert result["issue"]["title"] == "Bug"
    assert mock.posted == [
        (
            "/repos/octo/hello/issues",
            {"title": "Bug", "body": "details", "labels": ["bug", "p1"]},
        )
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"repo": "octo/hello"},
        {"repo": "octo/hello", "title": "   "},
        {"repo": "octo/hello", "title": "x" * 257},
        {"repo": "octo/hello", "title": "ok", "labels": "bug"},
        {"repo": "octo/hello", "title": "ok", "labels": ["  "]},
    ],
)
async def test_create_issue_rejects_bad_arguments(
    arguments: dict[str, Any],
) -> None:
    with pytest.raises(ProviderError) as raised:
        await _call(GithubMock(), "Github__create_issue", arguments)
    assert raised.value.code == "connector.invalid_arguments"


async def test_create_issue_comment_posts_body() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__create_issue_comment",
        {"repo": "octo/hello", "number": 7, "body": "thanks"},
    )
    assert result["comment"]["body"] == "thanks"
    assert mock.posted == [("/repos/octo/hello/issues/7/comments", {"body": "thanks"})]


async def test_create_pull_request_posts_branches() -> None:
    mock = GithubMock()
    result = await _call(
        mock,
        "Github__create_pull_request",
        {
            "repo": "octo/hello",
            "title": "Feature",
            "head": "feature",
            "base": "main",
            "body": "pr",
        },
    )
    assert result["pull_request"]["title"] == "Feature"
    assert result["pull_request"]["head"]["ref"] == "feature"
    assert mock.posted == [
        (
            "/repos/octo/hello/pulls",
            {
                "title": "Feature",
                "head": "feature",
                "base": "main",
                "body": "pr",
            },
        )
    ]


async def test_unknown_tool_and_non_object_arguments() -> None:
    with pytest.raises(ProviderError) as unknown:
        await _call(GithubMock(), "Github__missing", {})
    assert unknown.value.code == "connector.tool_not_found"

    with pytest.raises(ProviderError) as other_prefix:
        await _call(GithubMock(), "Other__get_repository", {})
    assert other_prefix.value.code == "connector.tool_not_found"

    with pytest.raises(ProviderError) as bad_args:
        await _call(
            GithubMock(),
            "Github__get_repository",
            ["octo/hello"],  # type: ignore[arg-type]
        )
    assert bad_args.value.code == "connector.invalid_arguments"


async def test_error_mapping_401_403_404_422_429_500() -> None:
    cases: list[tuple[httpx.Response, str, bool]] = [
        (httpx.Response(401, json={"message": "Bad"}), "connector.auth_expired", False),
        (
            rate_limit_response(),
            "connector.upstream_rate_limited",
            True,
        ),
        (
            httpx.Response(403, json={"message": "Forbidden"}),
            "connector.upstream_error",
            False,
        ),
        (
            rate_limit_response(status=429, retry_after=7),
            "connector.upstream_rate_limited",
            True,
        ),
        (
            httpx.Response(404, json={"message": "Not Found"}),
            "connector.invalid_arguments",
            False,
        ),
        (
            httpx.Response(422, json={"message": "Validation Failed"}),
            "connector.invalid_arguments",
            False,
        ),
        (
            httpx.Response(500, json={"message": "boom"}),
            "connector.upstream_unavailable",
            True,
        ),
        (
            httpx.Response(418, json={"message": "teapot"}),
            "connector.upstream_error",
            False,
        ),
    ]
    for response, code, retryable in cases:
        mock = GithubMock()
        mock.fail["/repos/octo/hello"] = response
        with pytest.raises(ProviderError) as raised:
            await _call_get_repo(mock)
        assert raised.value.code == code
        assert raised.value.retryable is retryable


async def test_rate_limit_hints_use_retry_after_and_reset() -> None:
    mock = GithubMock()
    mock.fail["/repos/octo/hello"] = rate_limit_response(status=429, retry_after=7)
    with pytest.raises(ProviderError) as retry_after:
        await _call_get_repo(mock)
    assert "7 秒" in (retry_after.value.hint or "")

    reset_mock = GithubMock()
    reset_mock.fail["/repos/octo/hello"] = rate_limit_response(reset_in=30)
    with pytest.raises(ProviderError) as reset:
        await _call_get_repo(reset_mock)
    hint = reset.value.hint or ""
    assert "秒后重试" in hint
    assert any(str(seconds) in hint for seconds in range(25, 31))


async def test_403_secondary_rate_limit_without_headers() -> None:
    mock = GithubMock()
    mock.fail["/repos/octo/hello"] = httpx.Response(
        403, json={"message": "You have exceeded a secondary rate limit"}
    )
    with pytest.raises(ProviderError) as raised:
        await _call_get_repo(mock)
    assert raised.value.code == "connector.upstream_rate_limited"


async def test_timeout_and_connection_error_are_retryable() -> None:
    mock = GithubMock()
    mock.raise_paths["/repos/octo/hello"] = httpx.ReadTimeout("slow")
    with pytest.raises(ProviderError) as timed_out:
        await _call_get_repo(mock)
    assert timed_out.value.code == "connector.upstream_timeout"
    assert timed_out.value.retryable is True

    broken = GithubMock()
    broken.raise_paths["/repos/octo/hello"] = httpx.ConnectError("down")
    with pytest.raises(ProviderError) as unavailable:
        await _call_get_repo(broken)
    assert unavailable.value.code == "connector.upstream_unavailable"
    assert unavailable.value.retryable is True


async def test_non_json_success_response_is_upstream_error() -> None:
    mock = GithubMock()
    mock.fail["/repos/octo/hello"] = httpx.Response(200, text="not json")
    with pytest.raises(ProviderError) as raised:
        await _call_get_repo(mock)
    assert raised.value.code == "connector.upstream_error"


async def test_422_hint_includes_upstream_message() -> None:
    mock = GithubMock()
    mock.fail["/repos/octo/hello"] = httpx.Response(
        422, json={"message": "Validation Failed: title is too long"}
    )
    with pytest.raises(ProviderError) as raised:
        await _call_get_repo(mock)
    assert "title is too long" in (raised.value.hint or "")
