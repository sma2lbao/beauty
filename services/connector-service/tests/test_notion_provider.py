"""内置 Notion provider:授权/身份/刷新/只读工具与错误映射(零网络)。

单测直接驱动 provider;集成用例走真实 app(authorize -> callback -> execute),
验证目录、凭证加密落库、内部执行链与 Notion-Version/Basic Auth 细节。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from common.service_testkit import app_client
from fastapi.testclient import TestClient

from connector.core.config import Settings
from connector.main import create_app
from connector.providers.base import Credentials
from connector.providers.errors import ProviderError
from connector.providers.notion import NotionProvider
from tests.conftest import _IDP, INTERNAL_HEADERS, TENANT, _settings
from tests.notion_mock import NotionMock, error_response

CREDS = Credentials(access_token="notion-at-1", refresh_token="notion-rt-1")


def _settings_obj(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "env": "dev",
        "providers_enabled": "notion",
        "notion_client_id": "nc",
        "notion_client_secret": "ns",
    }
    values.update(overrides)
    return Settings(**values)


def _provider(**overrides: Any) -> NotionProvider:
    return NotionProvider(_settings_obj(**overrides))


def _client(mock: NotionMock) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(mock.handler))


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_manifest() -> None:
    provider = _provider()
    assert provider.name == "notion"
    assert provider.prefix == "Notion"
    assert provider.display_name == "Notion"
    assert provider.configured is True
    assert provider.scopes() == ()
    assert provider.full_tool_name("search") == "Notion__search"
    assert [spec.name for spec in provider.tools] == [
        "search",
        "read_page",
        "read_blocks",
        "query_data_source",
        "create_page",
        "append_blocks",
    ]
    assert [str(spec.risk) for spec in provider.tools] == [
        "read",
        "read",
        "read",
        "read",
        "write",
        "write",
    ]
    with pytest.raises(KeyError):
        provider.tool_spec("Notion__delete_page")


def test_not_configured_without_credentials() -> None:
    provider = _provider(notion_client_id="", notion_client_secret="")
    assert provider.configured is False


def test_tenant_app_overrides_credentials() -> None:
    provider = _provider()
    tenant = provider.with_tenant_app("tenant-client", "tenant-secret")
    assert isinstance(tenant, NotionProvider)
    assert tenant is not provider
    url = tenant.authorize_url(
        state="s-1", code_challenge="ignored", redirect_uri="http://cb"
    )
    assert _query(url)["client_id"] == ["tenant-client"]


def test_authorize_url_has_no_pkce_and_sets_owner() -> None:
    url = _provider().authorize_url(
        state="s-1",
        code_challenge="challenge",
        redirect_uri="http://testserver/v1/oauth/notion/callback",
    )
    assert url.startswith("https://api.notion.com/v1/oauth/authorize?")
    query = _query(url)
    assert query["client_id"] == ["nc"]
    assert query["response_type"] == ["code"]
    assert query["owner"] == ["user"]
    assert query["state"] == ["s-1"]
    assert query["redirect_uri"] == ["http://testserver/v1/oauth/notion/callback"]
    assert "code_challenge" not in query


async def test_exchange_code_uses_basic_auth_and_returns_credentials() -> None:
    mock = NotionMock()
    provider = _provider()
    async with _client(mock) as client:
        creds = await provider.exchange_code(
            code="code-1",
            code_verifier="ignored",
            redirect_uri="http://testserver/v1/oauth/notion/callback",
            client=client,
        )
    expected = base64.b64encode(b"nc:ns").decode("ascii")
    assert mock.token_auth[0] == f"Basic {expected}"
    assert mock.token_calls[0] == {
        "grant_type": "authorization_code",
        "code": "code-1",
        "redirect_uri": "http://testserver/v1/oauth/notion/callback",
    }
    assert creds.access_token == "notion-at-1"
    assert creds.refresh_token == "notion-rt-1"
    assert creds.token_type == "bearer"
    assert creds.scope == ""
    # Notion 不返回 expires_in:不做按时间刷新,等 401
    assert creds.expires_at is None
    assert creds.refresh_expires_at is None


async def test_fetch_identity_composes_workspace_and_owner() -> None:
    mock = NotionMock()
    provider = _provider()
    async with _client(mock) as client:
        await provider.exchange_code(
            code="code-1",
            code_verifier="ignored",
            redirect_uri="http://cb",
            client=client,
        )
        identity = await provider.fetch_identity(CREDS, client)
    assert identity.account == "ws-1:owner-1"
    assert identity.display_name == "张三"
    assert identity.provider_account_id == "bot-1"
    assert identity.external_workspace == "Acme Workspace"
    assert identity.metadata["workspace_id"] == "ws-1"
    assert identity.metadata["owner_subject"] == "owner-1"
    method, url, auth, version = mock.api_calls[-1]
    assert (method, url) == ("GET", "https://api.notion.com/v1/users/me")
    assert auth == "Bearer notion-at-1"
    assert version == "2026-03-11"


async def test_refresh_uses_refresh_token_grant() -> None:
    mock = NotionMock()
    provider = _provider()
    async with _client(mock) as client:
        creds = await provider.refresh(CREDS, client)
    assert mock.token_calls[0] == {
        "grant_type": "refresh_token",
        "refresh_token": "notion-rt-1",
    }
    assert creds.access_token == "notion-at-1"
    assert creds.refresh_token == "notion-rt-1"


async def test_refresh_without_refresh_token_requires_reauth() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as excinfo:
            await _provider().refresh(Credentials(access_token="at"), client)
    assert excinfo.value.code == "connector.auth_expired"
    assert mock.token_calls == []


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (error_response(400, "invalid_grant"), "connector.auth_expired", False),
        (error_response(401, "unauthorized"), "connector.auth_expired", False),
        (
            error_response(429, "rate_limited", retry_after="7"),
            "connector.upstream_rate_limited",
            True,
        ),
        (
            error_response(500, "internal_server_error"),
            "connector.upstream_unavailable",
            True,
        ),
        (error_response(418, "teapot"), "connector.oauth_failed", False),
    ],
)
async def test_token_error_mapping(
    response: httpx.Response, code: str, retryable: bool
) -> None:
    mock = NotionMock()
    mock.fail["/oauth/token"] = response
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as excinfo:
            await _provider().exchange_code(
                code="code-1",
                code_verifier="ignored",
                redirect_uri="http://cb",
                client=client,
            )
    assert excinfo.value.code == code
    assert excinfo.value.retryable is retryable


async def test_search_maps_arguments_and_summarizes() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        result = await _provider().call(
            "Notion__search",
            {"query": "项目", "object_type": "page", "page_size": 999},
            CREDS.to_payload(),
            client,
        )
    method, url, _, _ = mock.api_calls[-1]
    assert (method, url) == ("POST", "https://api.notion.com/v1/search")
    assert result["has_more"] is False
    assert result["results"][0] == {
        "id": "page-1",
        "object": "page",
        "url": "https://www.notion.so/page-1",
        "last_edited_time": "2026-11-04T08:00:00.000Z",
        "title": "项目计划",
    }
    assert result["results"][1]["title"] == "任务表"


async def test_read_page_reads_properties() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        result = await _provider().call(
            "Notion__read_page", {"page_id": "page-1"}, CREDS.to_payload(), client
        )
    assert mock.api_calls[-1][1] == "https://api.notion.com/v1/pages/page-1"
    assert result["id"] == "page-1"
    assert result["properties"]["title"]["type"] == "title"
    assert result["parent"] == {"type": "workspace", "workspace": True}


async def test_read_blocks_pages_children() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        result = await _provider().call(
            "Notion__read_blocks",
            {"block_id": "page-1", "page_size": 2, "start_cursor": "c-1"},
            CREDS.to_payload(),
            client,
        )
    url = mock.api_calls[-1][1]
    assert url.startswith("https://api.notion.com/v1/blocks/page-1/children?")
    query = _query(url)
    assert query["page_size"] == ["2"]
    assert query["start_cursor"] == ["c-1"]
    assert result["results"][0]["id"] == "block-1"


async def test_query_data_source_passes_filter_and_sorts() -> None:
    mock = NotionMock()
    body = {"filter": {"property": "状态", "select": {"equals": "进行中"}}}
    async with _client(mock) as client:
        result = await _provider().call(
            "Notion__query_data_source",
            {
                "data_source_id": "ds-1",
                "filter": body["filter"],
                "sorts": [{"property": "日期", "direction": "descending"}],
            },
            CREDS.to_payload(),
            client,
        )
    assert mock.api_calls[-1][1] == "https://api.notion.com/v1/data_sources/ds-1/query"
    assert result["results"][0]["id"] == "row-1"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (error_response(401, "unauthorized"), "connector.auth_expired"),
        (error_response(403, "restricted_resource"), "connector.scope_missing"),
        (error_response(400, "invalid_request_url"), "connector.invalid_arguments"),
        (error_response(404, "object_not_found"), "connector.upstream_error"),
        (
            error_response(429, "rate_limited", retry_after="9"),
            "connector.upstream_rate_limited",
        ),
        (error_response(503, "service_unavailable"), "connector.upstream_unavailable"),
    ],
)
async def test_tool_error_mapping(response: httpx.Response, code: str) -> None:
    mock = NotionMock()
    mock.fail["/search"] = response
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as excinfo:
            await _provider().call("Notion__search", {}, CREDS.to_payload(), client)
    assert excinfo.value.code == code


async def test_tool_rate_limit_hint_mentions_retry_after() -> None:
    mock = NotionMock()
    mock.fail["/search"] = error_response(429, "rate_limited", retry_after="9")
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as excinfo:
            await _provider().call("Notion__search", {}, CREDS.to_payload(), client)
    assert "9 秒" in (excinfo.value.hint or "")


async def test_tool_timeout_and_network_error() -> None:
    provider = _provider()

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ProviderError) as excinfo:
            await provider.call("Notion__search", {}, CREDS.to_payload(), client)
    assert excinfo.value.code == "connector.upstream_timeout"

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        with pytest.raises(ProviderError) as excinfo:
            await provider.call("Notion__search", {}, CREDS.to_payload(), client)
    assert excinfo.value.code == "connector.upstream_unavailable"


async def test_create_page_posts_parent_title_and_paragraphs() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        result = await _provider().call(
            "Notion__create_page",
            {
                "parent_type": "data_source",
                "parent_id": "ds-1",
                "title": "周报",
                "title_property": "名称",
                "paragraphs": ["第一段", " 第二段 "],
            },
            CREDS.to_payload(),
            client,
        )
    method, url, body = mock.writes[-1]
    assert (method, url) == ("POST", "https://api.notion.com/v1/pages")
    assert body["parent"] == {"data_source_id": "ds-1"}
    assert body["properties"] == {
        "名称": {"title": [{"type": "text", "text": {"content": "周报"}}]}
    }
    assert [item["type"] for item in body["children"]] == ["paragraph", "paragraph"]
    assert (
        body["children"][1]["paragraph"]["rich_text"][0]["text"]["content"] == "第二段"
    )
    assert result["id"] == "page-new"
    assert result["parent"] == {"type": "page_id", "page_id": "page-1"}
    # 写请求同样带 Notion-Version
    assert mock.api_calls[-1][3] == "2026-03-11"


async def test_create_page_defaults_to_page_parent() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        await _provider().call(
            "Notion__create_page",
            {"parent_id": "page-1", "title": "子页面"},
            CREDS.to_payload(),
            client,
        )
    _, _, body = mock.writes[-1]
    assert body["parent"] == {"page_id": "page-1"}
    assert body["properties"] == {
        "title": {"title": [{"type": "text", "text": {"content": "子页面"}}]}
    }
    assert "children" not in body


async def test_append_blocks_patches_children() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        result = await _provider().call(
            "Notion__append_blocks",
            {"block_id": "page-1", "paragraphs": ["补一段"], "after": "block-1"},
            CREDS.to_payload(),
            client,
        )
    method, url, body = mock.writes[-1]
    assert (method, url) == (
        "PATCH",
        "https://api.notion.com/v1/blocks/page-1/children",
    )
    assert body["after"] == "block-1"
    assert (
        body["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == "补一段"
    )
    assert result["results"] == [{"id": "block-new-1", "type": "paragraph"}]


async def test_write_arguments_are_validated() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as empty:
            await _provider().call(
                "Notion__append_blocks",
                {"block_id": "page-1", "paragraphs": []},
                CREDS.to_payload(),
                client,
            )
        with pytest.raises(ProviderError) as parent:
            await _provider().call(
                "Notion__create_page",
                {"parent_id": "page-1", "title": "x", "parent_type": "workspace"},
                CREDS.to_payload(),
                client,
            )
        with pytest.raises(ProviderError) as bad_paragraphs:
            await _provider().call(
                "Notion__create_page",
                {"parent_id": "page-1", "title": "x", "paragraphs": [1]},
                CREDS.to_payload(),
                client,
            )
    assert empty.value.code == "connector.invalid_arguments"
    assert parent.value.code == "connector.invalid_arguments"
    assert bad_paragraphs.value.code == "connector.invalid_arguments"
    assert mock.writes == []


async def test_invalid_arguments() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as excinfo:
            await _provider().call("Notion__read_page", {}, CREDS.to_payload(), client)
    assert excinfo.value.code == "connector.invalid_arguments"
    assert mock.api_calls == []


# ---- 集成:真实 app 的授权与执行链 ----


def _notion_app(tmp_path: Path, mock: NotionMock) -> TestClient:
    app = create_app(
        _settings(
            tmp_path,
            providers_enabled="notion",
            notion_client_id="nc",
            notion_client_secret="ns",
        ),
        token_verifier=_IDP.verifier(),
        http_transport=httpx.MockTransport(mock.handler),
    )
    return app


def test_authorize_callback_and_execute_end_to_end(tmp_path: Path) -> None:
    mock = NotionMock()
    app = _notion_app(tmp_path, mock)
    with app_client(app) as client:
        catalog = client.get("/v1/connectors", headers=TENANT).json()
        notion = next(item for item in catalog["providers"] if item["name"] == "notion")
        assert notion["configured"] is True
        assert notion["connected"] is False
        risks = {tool["name"]: tool["risk"] for tool in notion["tools"]}
        assert risks["Notion__search"] == "read"
        assert risks["Notion__create_page"] == "write"
        assert risks["Notion__append_blocks"] == "write"

        resp = client.post("/v1/connectors/notion/authorize", headers=TENANT)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        url = payload["authorize_url"]
        assert url.startswith("https://api.notion.com/v1/oauth/authorize?")
        state = _query(url)["state"][0]

        callback = client.get(
            "/v1/oauth/notion/callback",
            params={"code": "code-1", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        params = {
            key: value[0] for key, value in _query(callback.headers["location"]).items()
        }
        assert params["provider"] == "notion"
        assert params["status"] == "connected"

        connection = client.get("/v1/connections", headers=TENANT).json()[0]
        assert connection["account"] == "ws-1:owner-1"
        assert connection["display_name"] == "张三"
        assert connection["external_workspace"] == "Acme Workspace"
        assert connection["status"] == "active"
        assert "credentials" not in connection

        # 换 token 用 Basic Auth,不是 PKCE
        expected = base64.b64encode(b"nc:ns").decode("ascii")
        assert mock.token_auth[0] == f"Basic {expected}"
        assert "code_verifier" not in mock.token_calls[0]

        executed = client.post(
            "/v1/connectors/notion/execute",
            headers=INTERNAL_HEADERS,
            json={"tool": "Notion__search", "arguments": {"query": "项目"}},
        )
        assert executed.status_code == 200, executed.text
        body = executed.json()
        assert body["ok"] is True
        assert body["provider"] == "notion"
        assert body["connection_id"] == connection["id"]
        assert body["data"]["results"][0]["title"] == "项目计划"
        _, _, auth, version = mock.api_calls[-1]
        assert auth == "Bearer notion-at-1"
        assert version == "2026-03-11"

        # 风险等级随内部目录透出:tool-service 据此要求人工确认
        descriptors = client.get(
            "/v1/internal/connectors/tools", headers=INTERNAL_HEADERS
        ).json()["tools"]
        by_name = {tool["name"]: tool for tool in descriptors}
        assert by_name["Notion__search"]["risk"] == "read"
        assert by_name["Notion__create_page"]["risk"] == "write"
        assert by_name["Notion__append_blocks"]["risk"] == "write"
        assert by_name["Notion__create_page"]["input_schema"]["required"] == [
            "parent_id",
            "title",
        ]


def test_execute_without_connection_is_tool_failure(tmp_path: Path) -> None:
    mock = NotionMock()
    app = _notion_app(tmp_path, mock)
    with app_client(app) as client:
        resp = client.post(
            "/v1/connectors/notion/execute",
            headers=INTERNAL_HEADERS,
            json={"tool": "Notion__search", "arguments": {}},
        )
    # 无连接时执行端点以平台错误返回 404,由 tool-service 翻成工具失败
    assert resp.status_code == 404
    assert resp.json()["code"] == "connector.connection_missing"
    assert mock.api_calls == []


# ---- 静态令牌连接(内部集成令牌,不需要 public integration) ----


async def test_connect_with_token_returns_static_credentials() -> None:
    mock = NotionMock()
    async with _client(mock) as client:
        credentials, identity = await _provider().connect_with_token(
            "ntn_secret_1", client
        )
    assert credentials.access_token == "ntn_secret_1"
    assert credentials.refresh_token is None
    assert credentials.expires_at is None
    assert identity.account == "integration:bot-1"
    assert identity.display_name == "张三"
    assert identity.external_workspace == "Acme Workspace"
    assert identity.metadata["connect"] == "token"
    method, url, auth, version = mock.api_calls[0]
    assert method == "GET"
    assert url == "https://api.notion.com/v1/users/me"
    assert auth == "Bearer ntn_secret_1"
    assert version == "2026-03-11"


def _token_app(tmp_path: Path, mock: NotionMock) -> TestClient:
    return create_app(
        _settings(tmp_path, providers_enabled="notion"),
        token_verifier=_IDP.verifier(),
        http_transport=httpx.MockTransport(mock.handler),
    )


def test_token_connect_creates_connection_without_app_credentials(
    tmp_path: Path,
) -> None:
    mock = NotionMock()
    app = _token_app(tmp_path, mock)
    with app_client(app) as client:
        notion = client.get("/v1/connectors", headers=TENANT).json()["providers"][0]
        assert notion["name"] == "notion"
        assert notion["token_connectable"] is True
        # 没配 client id/secret 也能连:令牌路径不依赖应用凭证
        assert notion["configured"] is False
        assert notion["connected"] is False

        resp = client.post(
            "/v1/connections",
            headers=TENANT,
            json={
                "provider": "notion",
                "token": "ntn_secret_1",
                "display_name": "我的 Notion",
            },
        )
        assert resp.status_code == 201, resp.text
        connection = resp.json()
        assert connection["provider"] == "notion"
        assert connection["account"] == "integration:bot-1"
        assert connection["auth_kind"] == "token"
        assert connection["display_name"] == "我的 Notion"
        assert connection["external_workspace"] == "Acme Workspace"
        assert connection["status"] == "active"
        assert connection["enabled"] is True
        assert connection["expires_at"] is None
        assert "credentials" not in connection
        assert connection["scopes"] == []
        assert mock.api_calls[0][2] == "Bearer ntn_secret_1"

        notion = client.get("/v1/connectors", headers=TENANT).json()["providers"][0]
        assert notion["connected"] is True
        assert notion["connection"]["id"] == connection["id"]

        # 工具链直接用这条令牌:执行面不需要额外授权
        executed = client.post(
            "/v1/connectors/notion/execute",
            headers=INTERNAL_HEADERS,
            json={"tool": "Notion__search", "arguments": {}},
        )
        assert executed.status_code == 200, executed.text
        body = executed.json()
        assert body["ok"] is True
        assert body["connection_id"] == connection["id"]
        assert mock.api_calls[-1][2] == "Bearer ntn_secret_1"

        # 同一集成的令牌重复连接复用同一行(account = integration:<bot_id>)
        again = client.post(
            "/v1/connections",
            headers=TENANT,
            json={"provider": "notion", "token": "ntn_secret_1"},
        )
        assert again.status_code == 201
        assert again.json()["id"] == connection["id"]
        assert len(client.get("/v1/connections", headers=TENANT).json()) == 1


def test_token_connect_rejects_invalid_token(tmp_path: Path) -> None:
    mock = NotionMock()
    mock.fail["/users/me"] = error_response(401, "unauthorized")
    app = _token_app(tmp_path, mock)
    with app_client(app) as client:
        resp = client.post(
            "/v1/connections",
            headers=TENANT,
            json={"provider": "notion", "token": "ntn_broken"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "connector.invalid_credentials"
        assert "令牌" in body["message"]
        assert body["details"]["retryable"] is False
        # 校验失败不落库
        assert client.get("/v1/connections", headers=TENANT).json() == []


def test_token_connect_rejects_unsupported_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = NotionMock()
    app = _token_app(tmp_path, mock)
    monkeypatch.setattr(NotionProvider, "token_connectable", False)
    with app_client(app) as client:
        resp = client.post(
            "/v1/connections",
            headers=TENANT,
            json={"provider": "notion", "token": "ntn_secret_1"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "connector.token_not_supported"
        assert mock.api_calls == []


def test_token_connect_unknown_provider(tmp_path: Path) -> None:
    mock = NotionMock()
    app = _token_app(tmp_path, mock)
    with app_client(app) as client:
        resp = client.post(
            "/v1/connections",
            headers=TENANT,
            json={"provider": "acme", "token": "ntn_secret_1"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "connector.provider_not_found"
        assert mock.api_calls == []
