"""GitHub provider 与平台链路:OAuth 回调、PAT、目录、执行、刷新与事件。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from common.service_testkit import app_client
from fastapi.testclient import TestClient

from connector.core.config import Settings
from connector.core.crypto import decrypt_credentials
from connector.main import create_app
from connector.repositories import ConnectionRepository
from connector.services import connections as connections_service
from connector.services.connections import refresh_if_expiring
from connector.services.events import CONNECTION_EXPIRED
from tests.conftest import (
    _IDP,
    ACCOUNT,
    CREDENTIAL_SECRET,
    INTERNAL_HEADERS,
    OTHER,
    _settings,
    insert_connection,
)
from tests.github_mock import GithubMock


def _app(tmp_path: Path, mock: GithubMock, **overrides: object) -> TestClient:
    values: dict[str, object] = {
        "providers_enabled": "github",
        "github_client_id": "cid",
        "github_client_secret": "secret",
    }
    values.update(overrides)
    settings: Settings = _settings(tmp_path, **values)
    return create_app(
        settings,
        token_verifier=_IDP.verifier(),
        http_transport=httpx.MockTransport(mock.handler),
    )


def _credentials(
    *,
    access_token: str = "ghu_old",
    refresh_token: str | None = "ghr_old",
    expired: bool = True,
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "scope": "",
        "expires_at": (
            now - timedelta(minutes=5) if expired else now + timedelta(hours=8)
        ).isoformat(),
        "refresh_expires_at": (now + timedelta(days=180)).isoformat(),
    }


def _stored_credentials(db_path: Path) -> dict[str, object]:
    with sqlite3.connect(db_path) as raw:
        row = raw.execute("SELECT credentials FROM connections").fetchone()
    return decrypt_credentials(CREDENTIAL_SECRET, json.loads(row[0]))


def _metadata(db_path: Path) -> dict[str, object]:
    with sqlite3.connect(db_path) as raw:
        row = raw.execute("SELECT metadata FROM connections").fetchone()
    return json.loads(row[0])


def _outbox_types(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as raw:
        rows = raw.execute("SELECT event_type FROM outbox_events").fetchall()
    return [row[0] for row in rows]


def _authorize_and_callback(
    client: TestClient, code: str = "good-code"
) -> httpx.Response:
    started = client.post("/v1/connectors/github/authorize", headers=ACCOUNT)
    assert started.status_code == 200, started.text
    authorize_url = started.json()["authorize_url"]
    state = parse_qs(urlparse(authorize_url).query)["state"][0]
    return client.get(
        f"/v1/oauth/github/callback?code={code}&state={state}",
        follow_redirects=False,
    )


def test_catalog_lists_github_even_without_credentials(tmp_path: Path) -> None:
    mock = GithubMock()
    app = _app(
        tmp_path,
        mock,
        github_client_id="",
        github_client_secret="",
    )
    with app_client(app) as client:
        provider = client.get("/v1/connectors", headers=ACCOUNT).json()["providers"][0]
    assert provider["name"] == "github"
    assert provider["display_name"] == "GitHub"
    assert provider["auth_kind"] == "user"
    assert provider["configured"] is False
    assert provider["token_connectable"] is True
    assert provider["connected"] is False
    assert provider["scopes"] == ["offline_access"]
    assert len(provider["tools"]) == 11
    risks = {tool["name"]: tool["risk"] for tool in provider["tools"]}
    assert risks["Github__get_repository"] == "read"
    assert risks["Github__create_issue"] == "write"
    assert risks["Github__create_issue_comment"] == "write"
    assert risks["Github__create_pull_request"] == "write"


def test_oauth_callback_creates_connection_without_credentials(
    tmp_path: Path,
) -> None:
    mock = GithubMock()
    mock.installations = []
    app = _app(tmp_path, mock)
    with app_client(app) as client:
        callback = _authorize_and_callback(client)
        assert callback.status_code == 302
        assert "status=connected" in callback.headers["location"]

        connections = client.get("/v1/connections", headers=ACCOUNT).json()
        assert len(connections) == 1
        connection = connections[0]
        assert connection["provider"] == "github"
        assert connection["account"] == "octocat"
        assert connection["auth_kind"] == "user"
        assert connection["status"] == "active"
        assert connection["scopes"] == []
        assert connection["expires_at"] is not None
        assert connection["refresh_expires_at"] is not None
        assert "credentials" not in connection

        detail = client.get(
            f"/v1/connections/{connection['id']}", headers=ACCOUNT
        ).json()
        assert "credentials" not in detail
        assert detail["provider_account_id"] == "42"

        # 0 安装:GitHub App 特有坑,写进 provider metadata 供排障
        metadata = _metadata(tmp_path / "test.db")
        assert metadata["has_installation"] is False
        assert metadata["installations"] == []

        # OAuth 授权落库的凭证可立即执行
        executed = client.post(
            "/v1/connectors/github/execute",
            headers=INTERNAL_HEADERS,
            json={
                "tool": "Github__get_repository",
                "arguments": {"repo": "octo/hello"},
            },
        ).json()
        assert executed["ok"] is True
        assert executed["data"]["repository"]["full_name"] == "octo/hello"
        assert mock.api_calls[-1]["auth"] == "Bearer ghu_access_1"


def test_pat_connect_execute_and_refresh_is_noop(tmp_path: Path) -> None:
    mock = GithubMock()
    app = _app(tmp_path, mock)
    with app_client(app) as client:
        created = client.post(
            "/v1/connections",
            headers=ACCOUNT,
            json={"provider": "github", "token": "ghp_pat_token"},
        )
        assert created.status_code == 201, created.text
        connection = created.json()
        assert connection["account"] == "octocat"
        assert connection["auth_kind"] == "token"
        assert connection["expires_at"] is None
        assert connection["refresh_expires_at"] is None
        assert connection["scopes"] == []
        assert "credentials" not in connection

        catalog = client.get("/v1/connectors", headers=ACCOUNT).json()["providers"][0]
        assert catalog["connected"] is True
        assert catalog["connection"]["id"] == connection["id"]

        executed = client.post(
            "/v1/connectors/github/execute",
            headers=INTERNAL_HEADERS,
            json={
                "tool": "Github__get_repository",
                "arguments": {"repo": "octo/hello"},
            },
        ).json()
        assert executed["ok"] is True
        assert executed["connection_id"] == connection["id"]
        assert mock.api_calls[-1]["auth"] == "Bearer ghp_pat_token"

        # PAT 没有 refresh token:刷新是 no-op,连接保持 active,不打 token 端点
        refreshed = client.post(
            f"/v1/connections/{connection['id']}/refresh", headers=ACCOUNT
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["status"] == "active"
        assert mock.token_calls == []

        checked = client.post(
            f"/v1/connections/{connection['id']}/check", headers=ACCOUNT
        )
        assert checked.status_code == 200
        assert checked.json()["last_error_code"] is None
        assert [call["path"] for call in mock.api_calls].count(
            "/user/installations"
        ) == 0


def test_pat_invalid_token_is_rejected_without_persisting(tmp_path: Path) -> None:
    mock = GithubMock()
    app = _app(tmp_path, mock)
    with app_client(app) as client:
        response = client.post(
            "/v1/connections",
            headers=ACCOUNT,
            json={"provider": "github", "token": "bad-token"},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "connector.invalid_credentials"
        assert body["details"]["retryable"] is False
        # 校验失败不落库
        assert client.get("/v1/connections", headers=ACCOUNT).json() == []


def test_internal_tool_descriptors_expose_github_tools(tmp_path: Path) -> None:
    mock = GithubMock()
    app = _app(tmp_path, mock)
    with app_client(app) as client:
        assert (
            client.post(
                "/v1/connections",
                headers=ACCOUNT,
                json={"provider": "github", "token": "ghp_pat_token"},
            ).status_code
            == 201
        )

        tools = client.get(
            "/v1/internal/connectors/tools", headers=INTERNAL_HEADERS
        ).json()["tools"]
        assert len(tools) == 11
        by_name = {tool["name"]: tool for tool in tools}
        assert set(by_name) == {
            f"Github__{name}"
            for name in (
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
            )
        }
        descriptor = by_name["Github__create_issue"]
        assert descriptor["executor"] == "connector"
        assert descriptor["connector"] == {"provider": "github"}
        assert descriptor["risk"] == "write"
        assert descriptor["kind"] == "function"
        assert descriptor["input_schema"]["required"] == ["repo", "title"]

        # 其他账号看不到 github 工具
        other = client.get(
            "/v1/internal/connectors/tools",
            headers={
                "X-Internal-Secret": INTERNAL_HEADERS["X-Internal-Secret"],
                "X-Account-Id": "t-2",
            },
        )
        assert other.json() == {"tools": []}
        assert (
            client.get("/v1/connectors", headers=OTHER).json()["providers"][0][
                "connected"
            ]
            is False
        )


def test_expiring_connection_rotates_refresh_token(tmp_path: Path) -> None:
    mock = GithubMock()
    app = _app(tmp_path, mock)
    db = tmp_path / "test.db"
    with app_client(app) as client:
        insert_connection(
            db,
            provider="github",
            account="octocat",
            credentials=_credentials(),
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        executed = client.post(
            "/v1/connectors/github/execute",
            headers=INTERNAL_HEADERS,
            json={
                "tool": "Github__get_repository",
                "arguments": {"repo": "octo/hello"},
            },
        ).json()
        assert executed["ok"] is True
        assert len(mock.token_calls) == 1
        assert mock.token_calls[0]["grant_type"] == "refresh_token"
        assert mock.token_calls[0]["refresh_token"] == "ghr_old"
        assert mock.token_calls[0]["client_secret"] == "secret"
        stored = _stored_credentials(db)
        assert stored["access_token"] == "ghu_access_2"
        assert stored["refresh_token"] == "ghr_refresh_2"
        assert mock.api_calls[-1]["auth"] == "Bearer ghu_access_2"

        # 第二次执行不再刷新(过期时间已更新)
        again = client.post(
            "/v1/connectors/github/execute",
            headers=INTERNAL_HEADERS,
            json={
                "tool": "Github__get_repository",
                "arguments": {"repo": "octo/hello"},
            },
        ).json()
        assert again["ok"] is True
        assert len(mock.token_calls) == 1


def test_refresh_rechecks_under_lock_without_calling_github_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """等锁期间连接已被别的请求刷新:锁内二次检查放行,不再换 token。

    SQLite 会忽略 ``SELECT ... FOR UPDATE``,因此这里按现有平台测试的方式
    注入 ``is_expiring`` 的两次返回值,精确覆盖单飞的锁内二次检查分支。
    """
    mock = GithubMock()
    app = _app(tmp_path, mock)
    db = tmp_path / "test.db"
    with app_client(app) as client:
        connection = insert_connection(
            db,
            provider="github",
            account="octocat",
            credentials=_credentials(),
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        calls = iter([True, False])
        monkeypatch.setattr(
            connections_service, "is_expiring", lambda *args, **kwargs: next(calls)
        )

        async def _run() -> str:
            factory = client.app.state.session_factory
            async with factory() as session:
                row = await ConnectionRepository(session).require_for_account(
                    "t-1", connection["id"]
                )
                refreshed = await refresh_if_expiring(
                    session,
                    client.app.state.settings,
                    "t-1",
                    row,
                    None,
                )
                return refreshed.status

        assert client.portal.call(_run) == "active"
        assert mock.token_calls == []


def test_bad_refresh_token_expires_connection_and_writes_outbox(
    tmp_path: Path,
) -> None:
    mock = GithubMock()
    app = _app(tmp_path, mock, outbox_enabled=True)
    db = tmp_path / "test.db"
    with app_client(app) as client:
        insert_connection(
            db,
            provider="github",
            account="octocat",
            credentials=_credentials(refresh_token="bad-refresh"),
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        executed = client.post(
            "/v1/connectors/github/execute",
            headers=INTERNAL_HEADERS,
            json={
                "tool": "Github__get_repository",
                "arguments": {"repo": "octo/hello"},
            },
        ).json()
        assert executed["ok"] is False
        assert executed["error"]["code"] == "connector.auth_expired"
        connection = client.get("/v1/connections", headers=ACCOUNT).json()[0]
        assert connection["status"] == "expired"
        assert connection["last_error_code"] == "connector.auth_expired"
    assert _outbox_types(db) == [CONNECTION_EXPIRED]
