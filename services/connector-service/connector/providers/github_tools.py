"""GitHub 的 11 个工具:ToolSpec 清单 + call 分派 + 结果整形。"""

from __future__ import annotations

from typing import Any

from connector.providers.base import Credentials, ToolRisk, ToolSpec
from connector.providers.errors import ProviderError
from connector.providers.github_api import (
    CONTENT_LIMIT,
    bool_arg,
    clean_path,
    decode_content,
    int_arg,
    labels_arg,
    optional_str,
    paging,
    repo_parts,
    repo_url,
    require_int,
    require_str,
    state_arg,
    truncate,
)

_REPO_PROP = {
    "type": "string",
    "description": "仓库,格式 owner/name,例如 octocat/Hello-World",
}
_PAGE_PROPS = {
    "page": {"type": "integer", "minimum": 1, "default": 1},
    "per_page": {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 30,
    },
}
_READ = ToolRisk.READ
_WRITE = ToolRisk.WRITE


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_repositories",
        title="列出仓库",
        description=(
            "列出当前 GitHub 用户可访问的仓库。可按 visibility/affiliation 过滤,"
            "按 created/updated/pushed/full_name 排序;分页 per_page 最大 100。"
        ),
        input_schema=_object(
            {
                "visibility": {
                    "type": "string",
                    "enum": ["all", "public", "private"],
                    "default": "all",
                },
                "affiliation": {
                    "type": "string",
                    "description": "逗号分隔:owner,collaborator,organization_member",
                },
                "sort": {
                    "type": "string",
                    "enum": ["created", "updated", "pushed", "full_name"],
                    "default": "full_name",
                },
                "direction": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "default": "asc",
                },
                **_PAGE_PROPS,
            },
            [],
        ),
        risk=_READ,
    ),
    ToolSpec(
        name="get_repository",
        title="读取仓库",
        description=(
            "读取单个仓库的元数据(README 可选)。repo 格式 owner/name;"
            "include_readme=true 时额外返回 README 正文。"
        ),
        input_schema=_object(
            {
                "repo": _REPO_PROP,
                "include_readme": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否返回 README 内容",
                },
            },
            ["repo"],
        ),
        risk=_READ,
    ),
    ToolSpec(
        name="get_file_contents",
        title="读取文件或目录",
        description=(
            "读取仓库中的文件内容或目录条目。repo 格式 owner/name;path 为仓库内路径,"
            "ref 为空时使用默认分支。文件以 base64 解码返回,"
            "超过 256KB 会截断并标记 truncated。"
        ),
        input_schema=_object(
            {
                "repo": _REPO_PROP,
                "path": {
                    "type": "string",
                    "description": "仓库内路径;空字符串表示仓库根目录",
                },
                "ref": {
                    "type": "string",
                    "description": "分支、标签或 commit SHA;缺省用默认分支",
                },
            },
            ["repo", "path"],
        ),
        risk=_READ,
    ),
    ToolSpec(
        name="search_code",
        title="搜索代码",
        description=(
            "使用 GitHub Search API 在代码中搜索。q 支持 GitHub 搜索语法,最长 256 字符;"
            "page/per_page 分页,per_page 最大 100。Search API 有独立且更紧的限流。"
        ),
        input_schema=_object(
            {
                "q": {"type": "string", "maxLength": 256},
                **_PAGE_PROPS,
            },
            ["q"],
        ),
        risk=_READ,
    ),
    ToolSpec(
        name="list_issues",
        title="列出 issue",
        description=(
            "列出仓库 issue。repo 格式 owner/name;state 为 open/closed/all;"
            "结果会过滤掉 GitHub 混入的 pull request。"
            "page/per_page 分页,per_page 最大 100。"
        ),
        input_schema=_object(
            {
                "repo": _REPO_PROP,
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "default": "open",
                },
                **_PAGE_PROPS,
            },
            ["repo"],
        ),
        risk=_READ,
    ),
    ToolSpec(
        name="get_issue",
        title="读取 issue",
        description=(
            "读取单个 issue 及其评论。repo 格式 owner/name;number 为 issue 编号;"
            "comment_limit 控制评论条数(1-100,默认 50)。"
        ),
        input_schema=_object(
            {
                "repo": _REPO_PROP,
                "number": {"type": "integer", "minimum": 1},
                "comment_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                },
            },
            ["repo", "number"],
        ),
        risk=_READ,
    ),
    ToolSpec(
        name="list_pull_requests",
        title="列出 pull request",
        description=(
            "列出仓库 pull request。repo 格式 owner/name;state 为 open/closed/all;"
            "page/per_page 分页,per_page 最大 100。"
        ),
        input_schema=_object(
            {
                "repo": _REPO_PROP,
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "default": "open",
                },
                **_PAGE_PROPS,
            },
            ["repo"],
        ),
        risk=_READ,
    ),
    ToolSpec(
        name="get_pull_request",
        title="读取 pull request",
        description=(
            "读取单个 pull request。repo 格式 owner/name;number 为 PR 编号;"
            "include_diff=true 时返回 diff 文本(超过 256KB 截断)。"
        ),
        input_schema=_object(
            {
                "repo": _REPO_PROP,
                "number": {"type": "integer", "minimum": 1},
                "include_diff": {"type": "boolean", "default": False},
            },
            ["repo", "number"],
        ),
        risk=_READ,
    ),
    ToolSpec(
        name="create_issue",
        title="创建 issue",
        description=(
            "在仓库创建 issue。repo 格式 owner/name;title 必填;body 为正文;"
            "labels 为标签名数组。写操作不幂等,同参数重放会重复创建。"
        ),
        input_schema=_object(
            {
                "repo": _REPO_PROP,
                "title": {"type": "string", "maxLength": 256},
                "body": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            ["repo", "title"],
        ),
        risk=_WRITE,
    ),
    ToolSpec(
        name="create_issue_comment",
        title="评论 issue",
        description=(
            "在 issue 或 pull request 下发表评论。repo 格式 owner/name;"
            "number 为 issue/PR 编号;body 必填。写操作不幂等。"
        ),
        input_schema=_object(
            {
                "repo": _REPO_PROP,
                "number": {"type": "integer", "minimum": 1},
                "body": {"type": "string"},
            },
            ["repo", "number", "body"],
        ),
        risk=_WRITE,
    ),
    ToolSpec(
        name="create_pull_request",
        title="创建 pull request",
        description=(
            "创建 pull request。repo 格式 owner/name;head 为源分支(或 owner:branch),"
            "base 为目标分支,二者必填;head 分支必须已存在。写操作不幂等。"
        ),
        input_schema=_object(
            {
                "repo": _REPO_PROP,
                "title": {"type": "string", "maxLength": 256},
                "head": {"type": "string"},
                "base": {"type": "string"},
                "body": {"type": "string"},
            },
            ["repo", "title", "head", "base"],
        ),
        risk=_WRITE,
    ),
)


class _ToolsMixin:
    """按 ToolSpec.name 分派;所有参数错误都是 connector.invalid_arguments。"""

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        credentials: Any,
        client: Any,
    ) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ProviderError("connector.invalid_arguments", "工具参数必须是对象")
        if isinstance(credentials, dict):
            # 执行面传的是解密后的 payload;provider 内部统一用 Credentials。
            credentials = Credentials.from_payload(credentials)
        try:
            spec = self.tool_spec(tool_name)
        except KeyError as exc:
            raise ProviderError(
                "connector.tool_not_found", f"未知工具: {tool_name}"
            ) from exc
        handler = getattr(self, f"_call_{spec.name}", None)
        if handler is None:
            raise ProviderError("connector.tool_not_found", f"工具未实现: {tool_name}")
        return await handler(arguments, credentials, client)

    # ── 读工具 ──────────────────────────────────────────────────────────

    async def _call_list_repositories(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        params: dict[str, Any] = paging(arguments)
        visibility = optional_str(arguments.get("visibility"))
        if visibility is not None:
            if visibility not in {"all", "public", "private"}:
                raise ProviderError(
                    "connector.invalid_arguments",
                    "visibility 只能是 all/public/private",
                )
            params["visibility"] = visibility
        affiliation = optional_str(arguments.get("affiliation"))
        if affiliation is not None:
            params["affiliation"] = affiliation
        sort = optional_str(arguments.get("sort"))
        if sort is not None:
            if sort not in {"created", "updated", "pushed", "full_name"}:
                raise ProviderError("connector.invalid_arguments", "sort 取值不合法")
            params["sort"] = sort
        direction = optional_str(arguments.get("direction"))
        if direction is not None:
            if direction not in {"asc", "desc"}:
                raise ProviderError(
                    "connector.invalid_arguments", "direction 只能是 asc/desc"
                )
            params["direction"] = direction
        data = await self._get_json(
            client,
            "https://api.github.com/user/repos",
            credentials,
            params=params,
        )
        items = data if isinstance(data, list) else []
        return {
            "repositories": [
                _repo_summary(item) for item in items if isinstance(item, dict)
            ]
        }

    async def _call_get_repository(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        owner, repo = repo_parts(arguments)
        data = await self._get_json(client, repo_url(owner, repo), credentials)
        readme = None
        if bool_arg(arguments.get("include_readme")):
            try:
                readme_data = await self._get_json(
                    client, repo_url(owner, repo, "/readme"), credentials
                )
            except ProviderError as exc:
                if exc.code != "connector.invalid_arguments":
                    raise
            else:
                readme = _file_payload(readme_data)
        return {
            "repository": _repo_detail(data) if isinstance(data, dict) else {},
            "readme": readme,
        }

    async def _call_get_file_contents(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        owner, repo = repo_parts(arguments)
        path = clean_path(arguments)
        ref = optional_str(arguments.get("ref"))
        params = {"ref": ref} if ref else None
        data = await self._get_json(
            client,
            repo_url(owner, repo, f"/contents/{path}"),
            credentials,
            params=params,
        )
        if isinstance(data, list):
            return {
                "type": "dir",
                "entries": [
                    _content_entry(item) for item in data if isinstance(item, dict)
                ],
            }
        if isinstance(data, dict):
            result = _file_payload(data)
            result["type"] = data.get("type") or "file"
            return result
        raise ProviderError(
            "connector.upstream_error", "GitHub 返回了无法识别的目录结构"
        )

    async def _call_search_code(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        query = require_str(arguments, "q", max_len=256)
        params: dict[str, Any] = paging(arguments)
        params["q"] = query
        data = await self._get_json(
            client,
            "https://api.github.com/search/code",
            credentials,
            params=params,
        )
        items = data.get("items") if isinstance(data, dict) else None
        return {
            "total_count": data.get("total_count") if isinstance(data, dict) else 0,
            "incomplete_results": bool(
                data.get("incomplete_results") if isinstance(data, dict) else False
            ),
            "items": [
                _code_item(item) for item in (items or []) if isinstance(item, dict)
            ],
        }

    async def _call_list_issues(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        owner, repo = repo_parts(arguments)
        params: dict[str, Any] = paging(arguments)
        params["state"] = state_arg(arguments)
        data = await self._get_json(
            client, repo_url(owner, repo, "/issues"), credentials, params=params
        )
        items = data if isinstance(data, list) else []
        return {
            "issues": [
                _issue_summary(item)
                for item in items
                if isinstance(item, dict) and "pull_request" not in item
            ]
        }

    async def _call_get_issue(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        owner, repo = repo_parts(arguments)
        number = require_int(arguments, "number")
        limit = int_arg(
            arguments.get("comment_limit"), default=50, name="comment_limit"
        )
        if limit < 1 or limit > 100:
            raise ProviderError(
                "connector.invalid_arguments",
                "comment_limit 必须在 1-100 之间",
            )
        issue = await self._get_json(
            client, repo_url(owner, repo, f"/issues/{number}"), credentials
        )
        comments = await self._get_json(
            client,
            repo_url(owner, repo, f"/issues/{number}/comments"),
            credentials,
            params={"per_page": limit},
        )
        items = comments if isinstance(comments, list) else []
        return {
            "issue": _issue_detail(issue) if isinstance(issue, dict) else {},
            "comments": [
                _comment_summary(item) for item in items if isinstance(item, dict)
            ],
            "comments_truncated": len(items) >= limit,
        }

    async def _call_list_pull_requests(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        owner, repo = repo_parts(arguments)
        params: dict[str, Any] = paging(arguments)
        params["state"] = state_arg(arguments)
        data = await self._get_json(
            client, repo_url(owner, repo, "/pulls"), credentials, params=params
        )
        items = data if isinstance(data, list) else []
        return {
            "pull_requests": [
                _pr_summary(item) for item in items if isinstance(item, dict)
            ]
        }

    async def _call_get_pull_request(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        owner, repo = repo_parts(arguments)
        number = require_int(arguments, "number")
        data = await self._get_json(
            client, repo_url(owner, repo, f"/pulls/{number}"), credentials
        )
        diff = None
        diff_truncated = False
        if bool_arg(arguments.get("include_diff")):
            text = await self._get_text(
                client,
                repo_url(owner, repo, f"/pulls/{number}"),
                credentials,
                accept="application/vnd.github.diff",
            )
            diff, diff_truncated = truncate(text, CONTENT_LIMIT)
        return {
            "pull_request": _pr_detail(data) if isinstance(data, dict) else {},
            "diff": diff,
            "diff_truncated": diff_truncated,
        }

    # ── 写工具 ──────────────────────────────────────────────────────────

    async def _call_create_issue(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        owner, repo = repo_parts(arguments)
        body: dict[str, Any] = {"title": require_str(arguments, "title", max_len=256)}
        text = optional_str(arguments.get("body"))
        if text is not None:
            body["body"] = text
        labels = labels_arg(arguments.get("labels"))
        if labels:
            body["labels"] = labels
        data = await self._post_json(
            client, repo_url(owner, repo, "/issues"), credentials, body
        )
        return {"issue": _issue_detail(data)}

    async def _call_create_issue_comment(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        owner, repo = repo_parts(arguments)
        number = require_int(arguments, "number")
        body = {"body": require_str(arguments, "body")}
        data = await self._post_json(
            client,
            repo_url(owner, repo, f"/issues/{number}/comments"),
            credentials,
            body,
        )
        return {"comment": _comment_summary(data)}

    async def _call_create_pull_request(
        self, arguments: dict[str, Any], credentials: Any, client: Any
    ) -> dict[str, Any]:
        owner, repo = repo_parts(arguments)
        body: dict[str, Any] = {
            "title": require_str(arguments, "title", max_len=256),
            "head": require_str(arguments, "head"),
            "base": require_str(arguments, "base"),
        }
        text = optional_str(arguments.get("body"))
        if text is not None:
            body["body"] = text
        data = await self._post_json(
            client, repo_url(owner, repo, "/pulls"), credentials, body
        )
        return {"pull_request": _pr_detail(data)}


# ── 结果整形(只挑稳定字段,避免把 GitHub 全量响应透传给模型)────────────


def _repo_summary(item: dict[str, Any]) -> dict[str, Any]:
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    return {
        "id": item.get("id"),
        "full_name": item.get("full_name"),
        "name": item.get("name"),
        "owner": owner.get("login"),
        "private": item.get("private"),
        "description": item.get("description"),
        "default_branch": item.get("default_branch"),
        "language": item.get("language"),
        "stargazers_count": item.get("stargazers_count"),
        "open_issues_count": item.get("open_issues_count"),
        "updated_at": item.get("updated_at"),
        "pushed_at": item.get("pushed_at"),
        "html_url": item.get("html_url"),
    }


def _repo_detail(item: dict[str, Any]) -> dict[str, Any]:
    data = _repo_summary(item)
    data.update(
        {
            "fork": item.get("fork"),
            "archived": item.get("archived"),
            "visibility": item.get("visibility"),
            "topics": item.get("topics") or [],
            "license": (
                item.get("license", {}).get("spdx_id")
                if isinstance(item.get("license"), dict)
                else None
            ),
            "created_at": item.get("created_at"),
            "forks_count": item.get("forks_count"),
            "watchers_count": item.get("watchers_count"),
        }
    )
    return data


def _file_payload(item: dict[str, Any]) -> dict[str, Any]:
    text, truncated, note = decode_content(item)
    return {
        "name": item.get("name"),
        "path": item.get("path"),
        "sha": item.get("sha"),
        "size": item.get("size"),
        "content": text,
        "truncated": truncated,
        "note": note,
    }


def _content_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name"),
        "path": item.get("path"),
        "type": item.get("type"),
        "size": item.get("size"),
        "sha": item.get("sha"),
    }


def _issue_summary(item: dict[str, Any]) -> dict[str, Any]:
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    labels = item.get("labels") if isinstance(item.get("labels"), list) else []
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "user": user.get("login"),
        "labels": [label.get("name") for label in labels if isinstance(label, dict)],
        "comments": item.get("comments"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "html_url": item.get("html_url"),
    }


def _issue_detail(item: dict[str, Any]) -> dict[str, Any]:
    data = _issue_summary(item)
    data["body"] = item.get("body")
    data["closed_at"] = item.get("closed_at")
    return data


def _comment_summary(item: dict[str, Any]) -> dict[str, Any]:
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    return {
        "id": item.get("id"),
        "user": user.get("login"),
        "body": item.get("body"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "html_url": item.get("html_url"),
    }


def _pr_summary(item: dict[str, Any]) -> dict[str, Any]:
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    head = item.get("head") if isinstance(item.get("head"), dict) else {}
    base = item.get("base") if isinstance(item.get("base"), dict) else {}
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "draft": item.get("draft"),
        "user": user.get("login"),
        "head": {"ref": head.get("ref"), "sha": head.get("sha")},
        "base": {"ref": base.get("ref")},
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "html_url": item.get("html_url"),
    }


def _pr_detail(item: dict[str, Any]) -> dict[str, Any]:
    data = _pr_summary(item)
    data.update(
        {
            "body": item.get("body"),
            "merged": item.get("merged"),
            "mergeable": item.get("mergeable"),
            "additions": item.get("additions"),
            "deletions": item.get("deletions"),
            "changed_files": item.get("changed_files"),
        }
    )
    return data


def _code_item(item: dict[str, Any]) -> dict[str, Any]:
    repository = (
        item.get("repository") if isinstance(item.get("repository"), dict) else {}
    )
    return {
        "name": item.get("name"),
        "path": item.get("path"),
        "sha": item.get("sha"),
        "html_url": item.get("html_url"),
        "repository": repository.get("full_name"),
    }


TOOLS = _TOOLS
