"""MCP 工具调用 401 刷新重试与 BFF 内部回调端点。"""

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import mcp as mcp_service
from tests.support.mcp_oauth import _patch_client, _server, _settings

# ------------------------------------------------ 工具调用:401 → 刷新 → 重试


def test_read_exposes_oauth_entry_after_auth_required() -> None:
    """首次授权入口:还没授权行,但 401 已把 last_status 落成 auth_required。"""
    before = mcp_service._to_read(_server(), "mcp-secret")
    assert before.has_oauth is False and before.oauth_authorized is False

    after = mcp_service._to_read(_server("auth_required"), "mcp-secret")
    assert after.has_oauth is True
    assert after.oauth_authorized is False


def test_handler_refreshes_token_and_retries_once(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    _patch_client(monkeypatch, calls)
    provider_calls: list[tuple[str, bool]] = []

    async def provider(server_id: str, force: bool) -> str | None:
        provider_calls.append((server_id, force))
        return "fresh-token" if force else "stale-token"

    reports: list[tuple[str, str]] = []

    async def report(server_id: str, detail: str) -> None:
        reports.append((server_id, detail))

    handler = mcp_service._make_http_handler(
        _server(), "echo", "mcp-secret", report, provider
    )
    assert asyncio.run(handler({"text": "hi"})) == "pong"
    assert calls == [("echo", "stale-token"), ("echo", "fresh-token")]
    assert provider_calls == [("server-1", False), ("server-1", True)]
    assert reports == []


def test_handler_without_provider_reports_auth_required(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    _patch_client(monkeypatch, calls)
    reports: list[tuple[str, str]] = []

    async def report(server_id: str, detail: str) -> None:
        reports.append((server_id, detail))

    handler = mcp_service._make_http_handler(
        _server(), "echo", "mcp-secret", report, None
    )
    payload = json.loads(asyncio.run(handler({})))
    assert payload["code"] == "mcp.auth_required"
    assert calls == [("echo", None)]
    assert reports and reports[0][0] == "server-1"
    assert "401" in reports[0][1]


def test_handler_does_not_retry_when_refresh_returns_same_token(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    _patch_client(monkeypatch, calls)

    async def provider(server_id: str, force: bool) -> str | None:
        return "stale-token"

    handler = mcp_service._make_http_handler(
        _server(), "echo", "mcp-secret", None, provider
    )
    payload = json.loads(asyncio.run(handler({})))
    assert payload["code"] == "mcp.auth_required"
    assert calls == [("echo", "stale-token")]


# ------------------------------------------------------- BFF 内部回调端点


def test_internal_callback_requires_internal_secret(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, internal_auth_secret="dev-internal-secret"))
    with TestClient(app) as client:
        denied = client.post(
            "/internal/mcp/oauth/callback",
            json={"state": "x", "code": "y"},
            headers={"X-Internal-Secret": "wrong"},
        )
        assert denied.status_code == 401
        assert denied.json()["code"] == "internal.forbidden"


def test_internal_callback_returns_error_redirect_data_for_bad_state(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path, internal_auth_secret="dev-internal-secret"))
    with TestClient(app) as client:
        resp = client.post(
            "/internal/mcp/oauth/callback",
            json={"state": "bad-state", "code": "auth-code"},
            headers={"X-Internal-Secret": "dev-internal-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["redirect_path"].startswith("/mcp?oauth=error")
