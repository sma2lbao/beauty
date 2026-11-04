"""执行面:工具路由、凭证零出口、惰性刷新单飞与错误信封。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tests.conftest import (
    INTERNAL_HEADERS,
    TENANT,
    FeishuMock,
    insert_connection,
)


def _execute(
    client: TestClient,
    tool: str,
    arguments: dict | None = None,
    *,
    provider: str = "feishu",
    headers: dict[str, str] = INTERNAL_HEADERS,
    run_id: str | None = "run-1",
) -> httpx.Response:
    return client.post(
        f"/v1/connectors/{provider}/execute",
        json={"tool": tool, "arguments": arguments or {}, "run_id": run_id},
        headers=headers,
    )


def test_execute_requires_internal_secret(client: TestClient) -> None:
    resp = _execute(client, "Feishu__get_me", headers={"X-Tenant-Id": "t-1"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "connector.internal_forbidden"


def test_execute_unknown_tool_and_provider(client: TestClient) -> None:
    unknown = _execute(client, "Feishu__drop_database")
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "connector.tool_not_found"

    provider = _execute(client, "Feishu__get_me", provider="notion")
    assert provider.status_code == 404
    assert provider.json()["code"] == "connector.provider_not_found"


def test_execute_without_connection(client: TestClient) -> None:
    resp = _execute(client, "Feishu__get_me")
    assert resp.status_code == 404
    assert resp.json()["code"] == "connector.connection_missing"


def test_execute_tools_happy_path(
    client: TestClient, feishu: FeishuMock, tmp_path: Path
) -> None:
    insert_connection(tmp_path / "test.db")

    read = _execute(client, "Feishu__read_document", {"document_id": "d1"})
    assert read.status_code == 200
    payload = read.json()
    assert payload["ok"] is True
    assert payload["data"] == {"document_id": "d1", "content": "hello"}
    assert payload["run_id"] == "run-1"
    assert payload["provider"] == "feishu"
    assert any("Bearer at-1" in call[2] for call in feishu.api_calls)

    me = _execute(client, "Feishu__get_me").json()
    assert me["data"]["open_id"] == "ou_1"

    files = _execute(client, "Feishu__list_files", {"page_size": 999}).json()
    assert files["data"]["files"][0]["name"] == "a.docx"
    assert "page_size=50" in feishu.api_calls[-1][1]

    blocks = _execute(
        client, "Feishu__list_document_blocks", {"document_id": "d1"}
    ).json()
    assert blocks["data"]["items"][0]["block_id"] == "b1"

    spaces = _execute(client, "Feishu__list_wiki_spaces").json()
    assert spaces["data"]["items"][0]["space_id"] == "sp1"

    nodes = _execute(client, "Feishu__list_wiki_nodes", {"space_id": "sp1"}).json()
    assert nodes["data"]["items"][0]["node_token"] == "n1"


def test_execute_missing_argument_is_tool_level_error(
    client: TestClient, tmp_path: Path
) -> None:
    insert_connection(tmp_path / "test.db")
    resp = _execute(client, "Feishu__read_document", {})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "connector.invalid_arguments"


def test_execute_auth_failure_marks_connection_expired(
    client: TestClient, feishu: FeishuMock, tmp_path: Path
) -> None:
    insert_connection(tmp_path / "test.db")
    feishu.fail["/raw_content"] = httpx.Response(401, json={"code": 99991663})
    payload = _execute(client, "Feishu__read_document", {"document_id": "d1"}).json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "connector.auth_expired"
    conn = client.get("/v1/connections", headers=TENANT).json()[0]
    assert conn["status"] == "expired"
    assert conn["last_error_code"] == "connector.auth_expired"


def test_execute_scope_and_rate_limit_errors(
    client: TestClient, feishu: FeishuMock, tmp_path: Path
) -> None:
    insert_connection(tmp_path / "test.db")
    feishu.fail["/drive/v1/files"] = httpx.Response(
        429, json={"code": 99991400}, headers={"x-ogw-ratelimit-reset": "7"}
    )
    limited = _execute(client, "Feishu__list_files").json()
    assert limited["error"]["code"] == "connector.upstream_rate_limited"
    assert limited["error"]["retryable"] is True
    assert "7" in limited["error"]["hint"]

    # scope 缺失归入「需要重新授权」:连接置 expired,后续执行按无连接处理
    feishu.fail["/drive/v1/files"] = httpx.Response(403, json={"code": 20027})
    scoped = _execute(client, "Feishu__list_files").json()
    assert scoped["error"]["code"] == "connector.scope_missing"
    conn = client.get("/v1/connections", headers=TENANT).json()[0]
    assert conn["status"] == "expired"
    assert conn["last_error_code"] == "connector.scope_missing"


def test_execute_transport_error_is_retryable(
    client: TestClient, feishu: FeishuMock, tmp_path: Path
) -> None:
    insert_connection(tmp_path / "test.db")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    original = feishu.handler
    feishu.handler = lambda request: (
        boom(request) if "/wiki/v2/spaces" in request.url.path else original(request)
    )
    # 重新绑定 transport 的处理器
    client.app.state.http_transport = httpx.MockTransport(feishu.handler)
    payload = _execute(client, "Feishu__list_wiki_spaces").json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "connector.upstream_timeout"
    assert payload["error"]["retryable"] is True


def test_execute_refreshes_expiring_token_once(
    client: TestClient, feishu: FeishuMock, tmp_path: Path
) -> None:
    db = tmp_path / "test.db"
    insert_connection(db, expires_at=datetime.now(UTC) - timedelta(minutes=5))
    first = _execute(client, "Feishu__read_document", {"document_id": "d1"})
    assert first.json()["ok"] is True
    assert feishu.refresh_tokens == 1
    assert any("Bearer at-2" in call[2] for call in feishu.api_calls)

    with sqlite3.connect(db) as raw:
        stored = json.loads(
            raw.execute("SELECT credentials FROM connections").fetchone()[0]
        )
    assert stored["ciphertext"]

    # 第二次执行不再刷新(锁内二次检查 + 过期时间已更新)
    second = _execute(client, "Feishu__get_me")
    assert second.json()["ok"] is True
    assert feishu.refresh_tokens == 1


def test_execute_refresh_failure_is_reported(
    client: TestClient, feishu: FeishuMock, tmp_path: Path
) -> None:
    insert_connection(
        tmp_path / "test.db",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    feishu.fail["/oauth/v3/token"] = httpx.Response(400, json={"code": 20005})
    payload = _execute(client, "Feishu__get_me").json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "connector.auth_expired"
    conn = client.get("/v1/connections", headers=TENANT).json()[0]
    assert conn["status"] == "expired"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "connector.auth_expired"
    conn = client.get("/v1/connections", headers=TENANT).json()[0]
    assert conn["status"] == "expired"


def test_execute_success_has_connection_id(
    client: TestClient, feishu: FeishuMock, tmp_path: Path
) -> None:
    """C2.4:执行信封带 connection_id 供 tool-service 审计。"""
    conn = insert_connection(tmp_path / "test.db")
    payload = _execute(client, "Feishu__get_me").json()
    assert payload["ok"] is True
    assert payload["connection_id"] == conn["id"]


def test_execute_tool_failure_has_connection_id(
    client: TestClient, feishu: FeishuMock, tmp_path: Path
) -> None:
    conn = insert_connection(tmp_path / "test.db")
    feishu.fail["/raw_content"] = httpx.Response(401, json={"code": 99991663})
    payload = _execute(
        client, "Feishu__read_document", {"document_id": "d1"}
    ).json()
    assert payload["ok"] is False
    assert payload["connection_id"] == conn["id"]

