"""P1-B 应用级 hooks 管理端点(docs/design/hooks.md §8):CRUD + 轮换 + 校验。

零网络:create_app 启动后重新注入 MockTransport + fake 解析器,出站请求与
DNS 都不触网(容器装配阶段会先用仅 settings 的 configure_hooks 覆盖一次)。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import hooks as hooks_service
from tests.conftest import ACCOUNT, OTHER, _settings, verifier

PUBLIC_ADDRESS = "93.184.216.34"
TARGET = "https://hooks.example.com/dsh"


def _resolver(host: str) -> list[str]:
    if host.startswith("unresolvable"):
        return []
    return [PUBLIC_ADDRESS]


def _refuse(request: httpx.Request) -> httpx.Response:  # pragma: no cover
    raise AssertionError(f"管理端点不应发出 hook 请求:{request.url}")


@contextmanager
def _hooks_client(
    tmp_path: Path, *, hooks_enabled: bool = True, **overrides: Any
) -> Iterator[TestClient]:
    settings = _settings(tmp_path, "hooks.db", hooks_enabled=hooks_enabled, **overrides)
    app = create_app(settings, token_verifier=verifier())
    with TestClient(app) as client:
        # 容器 _assemble_tools 已经用纯 settings 配置过一遍;这里补 transport/解析器,
        # 否则创建 hook 时真实 DNS 会被 pytest-socket 拦掉。
        hooks_service.configure_hooks(
            settings,
            transport=httpx.MockTransport(_refuse),
            resolver=_resolver,
        )
        yield client


def _create(client: TestClient, **payload: Any) -> dict[str, Any]:
    body = {"event": "PreToolUse", "target": TARGET, **payload}
    resp = client.post("/api/v1/hooks", json=body, headers=ACCOUNT)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _code(resp: httpx.Response) -> str:
    return str(resp.json().get("code") or "")


def test_requires_bearer_token(tmp_path: Path) -> None:
    with _hooks_client(tmp_path) as client:
        assert client.get("/api/v1/hooks").status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/hooks", None),
        ("POST", "/api/v1/hooks", {"event": "PreToolUse", "target": TARGET}),
        ("PATCH", "/api/v1/hooks/x", {"enabled": False}),
        ("DELETE", "/api/v1/hooks/x", None),
        ("POST", "/api/v1/hooks/x/rotate", None),
    ],
)
def test_all_endpoints_return_disabled_when_switch_off(
    tmp_path: Path, method: str, path: str, body: dict[str, Any] | None
) -> None:
    with _hooks_client(tmp_path, hooks_enabled=False) as client:
        resp = client.request(method, path, json=body, headers=ACCOUNT)
    assert resp.status_code == 409
    assert _code(resp) == "hooks.disabled"


def test_create_returns_secret_only_once(tmp_path: Path) -> None:
    with _hooks_client(tmp_path) as client:
        created = _create(client)
        assert created["secret"]
        assert created["matcher"] == "*"
        assert created["kind"] == "webhook"
        assert created["timeout_seconds"] == 3
        assert created["enabled"] is True

        listed = client.get("/api/v1/hooks", headers=ACCOUNT).json()
        assert [item["id"] for item in listed] == [created["id"]]
        assert "secret" not in listed[0]

        patched = client.patch(
            f"/api/v1/hooks/{created['id']}",
            json={"timeout_seconds": 9},
            headers=ACCOUNT,
        ).json()
        assert patched["timeout_seconds"] == 9
        assert "secret" not in patched


def test_list_filters_by_event_and_orders_by_creation(tmp_path: Path) -> None:
    with _hooks_client(tmp_path) as client:
        first = _create(client, event="PostToolUse")
        second = _create(client, event="PreToolUse")
        third = _create(client, event="SessionStart")

        listed = client.get("/api/v1/hooks", headers=ACCOUNT).json()
        assert [item["id"] for item in listed] == [
            first["id"],
            second["id"],
            third["id"],
        ]
        filtered = client.get(
            "/api/v1/hooks", params={"event": "PreToolUse"}, headers=ACCOUNT
        ).json()
        assert [item["id"] for item in filtered] == [second["id"]]


def test_patch_is_partial_and_revalidates_target(tmp_path: Path) -> None:
    with _hooks_client(tmp_path) as client:
        created = _create(client, matcher="read_*")
        patched = client.patch(
            f"/api/v1/hooks/{created['id']}",
            json={"matcher": "write_*", "enabled": False},
            headers=ACCOUNT,
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["matcher"] == "write_*"
        assert body["enabled"] is False
        assert body["target"] == TARGET  # 省略=保持

        bad = client.patch(
            f"/api/v1/hooks/{created['id']}",
            json={"target": "http://hooks.example.com/x"},
            headers=ACCOUNT,
        )
        assert bad.status_code == 422
        assert _code(bad) == "hooks.invalid_target"

        missing = client.patch(
            "/api/v1/hooks/nope", json={"enabled": True}, headers=ACCOUNT
        )
        assert missing.status_code == 404
        assert _code(missing) == "hooks.not_found"


def test_accounts_are_isolated(tmp_path: Path) -> None:
    with _hooks_client(tmp_path) as client:
        created = _create(client)
        assert client.get("/api/v1/hooks", headers=OTHER).json() == []
        patched = client.patch(
            f"/api/v1/hooks/{created['id']}", json={"enabled": False}, headers=OTHER
        )
        assert patched.status_code == 404
        assert _code(patched) == "hooks.not_found"


def test_delete_removes_row_and_is_idempotent_404(tmp_path: Path) -> None:
    with _hooks_client(tmp_path) as client:
        created = _create(client)
        assert (
            client.delete(f"/api/v1/hooks/{created['id']}", headers=ACCOUNT).status_code
            == 204
        )
        assert client.get("/api/v1/hooks", headers=ACCOUNT).json() == []
        again = client.delete(f"/api/v1/hooks/{created['id']}", headers=ACCOUNT)
        assert again.status_code == 404
        assert _code(again) == "hooks.not_found"


def test_rotate_issues_new_secret(tmp_path: Path) -> None:
    with _hooks_client(tmp_path) as client:
        created = _create(client)
        rotated = client.post(f"/api/v1/hooks/{created['id']}/rotate", headers=ACCOUNT)
        assert rotated.status_code == 200
        assert rotated.json()["secret"] != created["secret"]
        # 轮换后列表仍不回显 secret
        listed = client.get("/api/v1/hooks", headers=ACCOUNT).json()
        assert "secret" not in listed[0]

        missing = client.post("/api/v1/hooks/nope/rotate", headers=ACCOUNT)
        assert missing.status_code == 404


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ({"event": "Nope", "target": TARGET}, None),
        (
            {"event": "PreToolUse", "target": "ftp://hooks.example.com/x"},
            "hooks.invalid_target",
        ),
        (
            {"event": "PreToolUse", "target": "https://10.0.0.5/x"},
            "hooks.invalid_target",
        ),
        (
            {"event": "PreToolUse", "target": "https://127.0.0.1/x"},
            "hooks.invalid_target",
        ),
        (
            {"event": "PreToolUse", "target": "https://localhost/x"},
            "hooks.invalid_target",
        ),
        (
            {"event": "PreToolUse", "target": "https://unresolvable.example/x"},
            "hooks.invalid_target",
        ),
        ({"event": "PreToolUse", "target": TARGET, "matcher": "m" * 201}, None),
        ({"event": "PreToolUse", "target": TARGET, "timeout_seconds": 0}, None),
        ({"event": "PreToolUse", "target": TARGET, "timeout_seconds": 31}, None),
        ({"event": "PreToolUse", "target": TARGET, "extra": "x"}, None),
        ({"event": "PreToolUse", "target": ""}, None),
    ],
)
def test_create_rejects_invalid_payloads(
    tmp_path: Path, payload: dict[str, Any], expected_code: str | None
) -> None:
    with _hooks_client(tmp_path) as client:
        resp = client.post("/api/v1/hooks", json=payload, headers=ACCOUNT)
        assert resp.status_code == 422, resp.text
        if expected_code is not None:
            assert _code(resp) == expected_code
        # 校验失败不应落库
        assert client.get("/api/v1/hooks", headers=ACCOUNT).json() == []


def test_http_target_allowed_only_when_debug_switch_on(tmp_path: Path) -> None:
    with _hooks_client(tmp_path, hooks_allow_http=True) as client:
        created = _create(client, target="http://hooks.example.com/dsh")
        assert created["target"] == "http://hooks.example.com/dsh"


def test_max_per_account_is_enforced(tmp_path: Path) -> None:
    with _hooks_client(tmp_path, hooks_max_per_account=2) as client:
        _create(client)
        _create(client)
        third = client.post(
            "/api/v1/hooks",
            json={"event": "PreToolUse", "target": TARGET},
            headers=ACCOUNT,
        )
        assert third.status_code == 409
        assert _code(third) == "hooks.too_many"
        assert len(client.get("/api/v1/hooks", headers=ACCOUNT).json()) == 2
