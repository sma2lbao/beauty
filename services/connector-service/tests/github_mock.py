"""GitHub API 的进程内 MockTransport:零网络、记录请求、可注入失败。"""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import parse_qs

import httpx

API = "https://api.github.com"
TOKEN_URL = "https://github.com/login/oauth/access_token"
REFRESH_TOKEN_EXPIRES_IN = 15_897_600

REPO = {
    "id": 1,
    "full_name": "octo/hello",
    "name": "hello",
    "owner": {"login": "octo"},
    "private": False,
    "description": "demo repo",
    "default_branch": "main",
    "language": "Python",
    "stargazers_count": 3,
    "open_issues_count": 2,
    "updated_at": "2026-01-01T00:00:00Z",
    "pushed_at": "2026-01-02T00:00:00Z",
    "html_url": "https://github.com/octo/hello",
    "fork": False,
    "archived": False,
    "visibility": "public",
    "topics": ["demo"],
    "license": {"spdx_id": "MIT"},
    "created_at": "2025-01-01T00:00:00Z",
    "forks_count": 1,
    "watchers_count": 2,
}

ISSUE = {
    "number": 7,
    "title": "Bug",
    "state": "open",
    "user": {"login": "octocat"},
    "labels": [{"name": "bug"}],
    "comments": 2,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "html_url": "https://github.com/octo/hello/issues/7",
    "body": "body",
    "closed_at": None,
}

PULL = {
    "number": 3,
    "title": "Feature",
    "state": "open",
    "draft": False,
    "user": {"login": "octocat"},
    "head": {"ref": "feature", "sha": "abc123"},
    "base": {"ref": "main"},
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "html_url": "https://github.com/octo/hello/pull/3",
    "body": "pr body",
    "merged": False,
    "mergeable": True,
    "additions": 3,
    "deletions": 1,
    "changed_files": 2,
}


def token_payload(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "access_token": "ghu_access_1",
        "expires_in": 28800,
        "refresh_token": "ghr_refresh_1",
        "refresh_token_expires_in": REFRESH_TOKEN_EXPIRES_IN,
        "token_type": "bearer",
        "scope": "",
    }
    values.update(overrides)
    return values


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


class GithubMock:
    """记录 token 表单与 REST 调用;默认返回一组固定对象。"""

    def __init__(self) -> None:
        self.token_calls: list[dict[str, str]] = []
        self.token_urls: list[str] = []
        self.api_calls: list[dict[str, Any]] = []
        self.posted: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, httpx.Response] = {}
        self.raise_paths: dict[str, Exception] = {}
        self.token_status = 200
        self.token_body: dict[str, Any] | None = None
        self.token_raise: Exception | None = None
        self.installations: list[dict[str, Any]] = [
            {"id": 11, "account": {"login": "octo"}}
        ]
        self.token_response: dict[str, Any] | None = None
        self.refresh_response: dict[str, Any] | None = None
        self.diff_text = "diff --git a/a.txt b/a.txt\n+hello\n"
        self.huge_size = 256 * 1024 + 10

    # ── 构造 ────────────────────────────────────────────────────────────

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path
        if path == "/login/oauth/access_token":
            if self.token_raise is not None:
                raise self.token_raise
            return self._token(request)
        if path in self.raise_paths:
            raise self.raise_paths[path]
        if path in self.fail:
            return self.fail[path]
        auth = request.headers.get("authorization", "")
        if path == "/user" and "bad-token" in auth:
            return httpx.Response(401, json={"message": "Bad credentials"})
        self.api_calls.append(
            {
                "method": request.method,
                "path": path,
                "auth": auth,
                "accept": request.headers.get("accept"),
                "query": dict(url.params),
            }
        )
        if path == "/repos/octo/hello/pulls/3" and "diff" in request.headers.get(
            "accept", ""
        ):
            return httpx.Response(200, text=self.diff_text)
        if request.method == "POST":
            body = json.loads(request.content.decode() or "{}")
            self.posted.append((path, body))
            return self._post(path, body)
        return self._get(path, dict(url.params))

    # ── token ───────────────────────────────────────────────────────────

    def _token(self, request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        flat = {key: values[0] for key, values in body.items()}
        self.token_calls.append(flat)
        self.token_urls.append(str(request.url))
        if self.token_status != 200:
            return httpx.Response(
                self.token_status,
                json=self.token_body or {"error": "server_error", "message": "boom"},
            )
        code = flat.get("code")
        if code == "bad-code":
            return httpx.Response(200, json={"error": "bad_verification_code"})
        if code == "no-refresh":
            return httpx.Response(200, json=token_payload(refresh_token=None))
        if code == "no-expiry":
            return httpx.Response(200, json=token_payload(expires_in=None))
        if flat.get("grant_type") == "refresh_token":
            if self.refresh_response is not None:
                return httpx.Response(200, json=self.refresh_response)
            if flat.get("refresh_token") == "bad-refresh":
                return httpx.Response(200, json={"error": "bad_refresh_token"})
            return httpx.Response(
                200,
                json=token_payload(
                    access_token="ghu_access_2", refresh_token="ghr_refresh_2"
                ),
            )
        return httpx.Response(200, json=self.token_response or token_payload())

    # ── GET ─────────────────────────────────────────────────────────────

    def _get(self, path: str, query: dict[str, Any]) -> httpx.Response:
        normalized = path.rstrip("/")
        if normalized == "/user":
            return httpx.Response(
                200, json={"id": 42, "login": "octocat", "name": "Octo Cat"}
            )
        if normalized == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "total_count": len(self.installations),
                    "installations": self.installations,
                },
            )
        if normalized == "/user/repos":
            return httpx.Response(200, json=[REPO])
        if normalized == "/search/code":
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "incomplete_results": False,
                    "items": [
                        {
                            "name": "hello.py",
                            "path": "hello.py",
                            "sha": "s1",
                            "html_url": "https://github.com/octo/hello/blob/main/hello.py",
                            "repository": {"full_name": "octo/hello"},
                        }
                    ],
                },
            )
        if normalized == "/repos/octo/hello":
            return httpx.Response(200, json=REPO)
        if normalized == "/repos/octo/hello/readme":
            return httpx.Response(
                200,
                json={
                    "name": "README.md",
                    "path": "README.md",
                    "sha": "readme-sha",
                    "size": 7,
                    "encoding": "base64",
                    "content": b64("# Hello"),
                },
            )
        if normalized == "/repos/octo/hello/contents":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "README.md",
                        "path": "README.md",
                        "type": "file",
                        "size": 7,
                        "sha": "readme-sha",
                    },
                    {
                        "name": "src",
                        "path": "src",
                        "type": "dir",
                        "size": 0,
                        "sha": "dir-sha",
                    },
                ],
            )
        if normalized == "/repos/octo/hello/contents/README.md":
            return httpx.Response(
                200,
                json={
                    "name": "README.md",
                    "path": "README.md",
                    "sha": "readme-sha",
                    "size": 7,
                    "encoding": "base64",
                    "content": b64("# Hello"),
                },
            )
        if normalized == "/repos/octo/hello/contents/big.bin":
            return httpx.Response(
                200,
                json={
                    "name": "big.bin",
                    "path": "big.bin",
                    "sha": "big-sha",
                    "size": 2 * 1024 * 1024,
                    "encoding": "none",
                    "content": "",
                },
            )
        if normalized == "/repos/octo/hello/contents/huge.txt":
            return httpx.Response(
                200,
                json={
                    "name": "huge.txt",
                    "path": "huge.txt",
                    "sha": "huge-sha",
                    "size": self.huge_size,
                    "encoding": "base64",
                    "content": b64("x" * self.huge_size),
                },
            )
        if normalized == "/repos/octo/hello/issues":
            return httpx.Response(
                200, json=[ISSUE, {**ISSUE, "number": 8, "pull_request": {}}]
            )
        if normalized == "/repos/octo/hello/issues/7":
            return httpx.Response(200, json=ISSUE)
        if normalized == "/repos/octo/hello/issues/7/comments":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "user": {"login": "a"},
                        "body": "first",
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                        "html_url": "https://github.com/octo/hello/issues/7#c1",
                    },
                    {
                        "id": 2,
                        "user": {"login": "b"},
                        "body": "second",
                        "created_at": "2026-01-02T00:00:00Z",
                        "updated_at": "2026-01-02T00:00:00Z",
                        "html_url": "https://github.com/octo/hello/issues/7#c2",
                    },
                ],
            )
        if normalized == "/repos/octo/hello/pulls":
            return httpx.Response(200, json=[PULL])
        if normalized == "/repos/octo/hello/pulls/3":
            return httpx.Response(200, json=PULL)
        return httpx.Response(404, json={"message": "Not Found"})

    # ── POST ────────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        if path == "/repos/octo/hello/issues":
            return httpx.Response(
                200,
                json={**ISSUE, "title": body.get("title"), "body": body.get("body")},
            )
        if path == "/repos/octo/hello/issues/7/comments":
            return httpx.Response(
                200,
                json={
                    "id": 3,
                    "user": {"login": "octocat"},
                    "body": body.get("body"),
                    "created_at": "2026-01-03T00:00:00Z",
                    "updated_at": "2026-01-03T00:00:00Z",
                    "html_url": "https://github.com/octo/hello/issues/7#c3",
                },
            )
        if path == "/repos/octo/hello/pulls":
            return httpx.Response(
                200,
                json={
                    **PULL,
                    "title": body.get("title"),
                    "head": {"ref": body.get("head"), "sha": "abc123"},
                    "base": {"ref": body.get("base")},
                },
            )
        return httpx.Response(404, json={"message": "Not Found"})


def rate_limit_response(
    *, status: int = 403, retry_after: int | None = None, reset_in: int = 42
) -> httpx.Response:
    headers = {
        "x-ratelimit-remaining": "0",
        "x-ratelimit-reset": str(int(time.time()) + reset_in),
    }
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    return httpx.Response(
        status, headers=headers, json={"message": "API rate limit exceeded"}
    )
