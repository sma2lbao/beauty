"""沙箱 P1:生命周期、租户隔离、路径校验、过期回收、provider 故障收口。"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from database.session import session_scope
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.conftest import (
    OTHER,
    TENANT,
    FakeSandboxProvider,
    _settings,
    verifier,
)
from workspace.main import create_app
from workspace.models.sandbox import SandboxInstance
from workspace.services.sandbox import reap_expired


def _create(client: TestClient, **payload: Any) -> dict[str, Any]:
    response = client.post("/v1/sandboxes", json=payload, headers=TENANT)
    assert response.status_code == 201, response.text
    return response.json()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _set_expired(client: TestClient, sandbox_id: str) -> None:
    """把实例的 expires_at 改到过去(绕过 TTL 等待)。"""
    factory = client.app.state.session_factory
    async with session_scope(factory) as session:
        record = await session.get(SandboxInstance, sandbox_id)
        assert record is not None
        record.expires_at = datetime.now(UTC) - timedelta(seconds=5)


def test_sandbox_lifecycle(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    """创建 → 读 → exec → 写/读文件 → touch → 删除。"""
    created = _create(client, kind="skill", ttl_seconds=600)
    assert created["status"] == "running"
    assert created["provider"] == "fake"
    assert created["kind"] == "skill"
    assert created["name"].startswith("bm-")
    assert len(created["name"]) == len("bm-") + 20
    assert created["image"] == "python"
    assert created["cpus"] == 1.0
    assert created["memory_mb"] == 512
    assert created["network"] == "deny"
    assert created["last_error"] is None

    spec = sandbox_provider.created[0]
    assert spec.name == created["name"]
    assert spec.tenant_id == "t-1"
    assert spec.ttl_seconds == 600
    assert spec.network == "deny"

    fetched = client.get(f"/v1/sandboxes/{created['id']}", headers=TENANT)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    exec_response = client.post(
        f"/v1/sandboxes/{created['id']}/exec",
        json={
            "argv": ["python3", "-c", "print(1)"],
            "env": {"A": "B"},
            "timeout_seconds": 5,
        },
        headers=TENANT,
    )
    assert exec_response.status_code == 200
    assert exec_response.json() == {
        "exit_code": 0,
        "stdout": "hello from sandbox\n",
        "stderr": "",
        "duration_ms": 7,
        "truncated": False,
    }
    assert sandbox_provider.last_exec["argv"] == ["python3", "-c", "print(1)"]
    assert sandbox_provider.last_exec["env"] == {"A": "B"}
    assert sandbox_provider.last_exec["timeout_seconds"] == 5

    payload = b"print('hi')\n"
    write = client.put(
        f"/v1/sandboxes/{created['id']}/files",
        json={
            "path": "/workspace/main.py",
            "content_base64": base64.b64encode(payload).decode("ascii"),
        },
        headers=TENANT,
    )
    assert write.status_code == 204
    read = client.get(
        f"/v1/sandboxes/{created['id']}/files",
        params={"path": "/workspace/main.py"},
        headers=TENANT,
    )
    assert read.status_code == 200
    body = read.json()
    assert base64.b64decode(body["content_base64"]) == payload
    assert body["size_bytes"] == len(payload)

    touched = client.post(f"/v1/sandboxes/{created['id']}/touch", headers=TENANT)
    assert touched.status_code == 200
    assert _parse_utc(touched.json()["expires_at"]) > _parse_utc(created["expires_at"])

    deleted = client.delete(f"/v1/sandboxes/{created['id']}", headers=TENANT)
    assert deleted.status_code == 204
    assert (
        client.get(f"/v1/sandboxes/{created['id']}", headers=TENANT).status_code == 404
    )
    assert sandbox_provider.destroyed == [created["name"]]


def test_create_uses_settings_defaults(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    """请求不传资源时,用 config 里的 sandbox_default_* 默认值。"""
    settings = _settings(
        tmp_path,
        "defaults.db",
        sandbox_default_image="alpine",
        sandbox_default_cpus=2.0,
        sandbox_default_memory_mb=1024,
        sandbox_network="public",
    )
    app = create_app(
        settings, token_verifier=verifier(), sandbox_provider=sandbox_provider
    )
    with TestClient(app) as test_client:
        created = _create(test_client)
        assert created["image"] == "alpine"
        assert created["cpus"] == 2.0
        assert created["memory_mb"] == 1024
        assert created["network"] == "public"
        assert sandbox_provider.created[0].cpus == 2.0
        assert sandbox_provider.created[0].memory_mb == 1024


@pytest.mark.parametrize(
    "payload",
    [
        {"ttl_seconds": 7201},
        {"cpus": 9},
        {"memory_mb": 8192},
        {"network": "host"},
        {"kind": "unknown"},
    ],
)
def test_create_rejects_invalid_payload(
    client: TestClient, payload: dict[str, Any]
) -> None:
    assert client.post("/v1/sandboxes", json=payload, headers=TENANT).status_code == 422


def test_exec_rejects_invalid_payload(client: TestClient) -> None:
    sandbox = _create(client)
    url = f"/v1/sandboxes/{sandbox['id']}/exec"
    assert client.post(url, json={"argv": []}, headers=TENANT).status_code == 422
    assert (
        client.post(
            url, json={"argv": ["true"], "cwd": "../etc"}, headers=TENANT
        ).status_code
        == 422
    )


def test_sandbox_isolation(client: TestClient) -> None:
    sandbox = _create(client)
    sandbox_id = sandbox["id"]

    assert client.get(f"/v1/sandboxes/{sandbox_id}", headers=OTHER).status_code == 404
    execution = client.post(
        f"/v1/sandboxes/{sandbox_id}/exec",
        json={"argv": ["true"]},
        headers=OTHER,
    )
    assert execution.status_code == 404
    assert execution.json()["code"] == "sandbox.not_found"
    assert (
        client.delete(f"/v1/sandboxes/{sandbox_id}", headers=OTHER).status_code == 404
    )
    assert client.get(f"/v1/sandboxes/{sandbox_id}", headers=TENANT).status_code == 200
    assert (
        client.get("/v1/sandboxes/missing", headers=TENANT).json()["code"]
        == "sandbox.not_found"
    )


def test_file_path_traversal_rejected(client: TestClient) -> None:
    sandbox = _create(client)
    base = f"/v1/sandboxes/{sandbox['id']}/files"

    for path in ("/etc/passwd", "/workspace/../etc/passwd", "/workspace/a/../../b"):
        write = client.put(
            base,
            json={"path": path, "content_base64": ""},
            headers=TENANT,
        )
        assert write.status_code == 400, path
        assert write.json()["code"] == "sandbox.path_invalid"

        read = client.get(base, params={"path": path}, headers=TENANT)
        assert read.status_code == 400, path
        assert read.json()["code"] == "sandbox.path_invalid"


def test_missing_file_is_404(client: TestClient) -> None:
    sandbox = _create(client)
    response = client.get(
        f"/v1/sandboxes/{sandbox['id']}/files",
        params={"path": "/workspace/nope.txt"},
        headers=TENANT,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "sandbox.file_not_found"


async def test_expired_sandbox_exec_conflict(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client, ttl_seconds=600)
    await _set_expired(client, sandbox["id"])

    response = client.post(
        f"/v1/sandboxes/{sandbox['id']}/exec",
        json={"argv": ["true"]},
        headers=TENANT,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "sandbox.expired"
    # 过期即回收:provider 收到 destroy,DB 状态持久化为 stopped
    assert sandbox_provider.destroyed == [sandbox["name"]]
    fetched = client.get(f"/v1/sandboxes/{sandbox['id']}", headers=TENANT).json()
    assert fetched["status"] == "stopped"


async def test_reap_expired_destroys_only_expired(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    alive = _create(client, ttl_seconds=600)
    expired = _create(client, ttl_seconds=600)
    await _set_expired(client, expired["id"])

    reaped = await reap_expired(client.app.state.session_factory, sandbox_provider)
    assert reaped == 1
    assert sandbox_provider.destroyed == [expired["name"]]
    assert (
        client.get(f"/v1/sandboxes/{expired['id']}", headers=TENANT).json()["status"]
        == "stopped"
    )
    assert (
        client.get(f"/v1/sandboxes/{alive['id']}", headers=TENANT).json()["status"]
        == "running"
    )


async def test_reap_failure_keeps_last_error(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    expired = _create(client, ttl_seconds=600)
    await _set_expired(client, expired["id"])
    sandbox_provider.fail_destroy = True

    reaped = await reap_expired(client.app.state.session_factory, sandbox_provider)
    assert reaped == 0
    fetched = client.get(f"/v1/sandboxes/{expired['id']}", headers=TENANT).json()
    assert fetched["status"] == "running"
    assert fetched["last_error"]


async def test_provider_create_failure_is_502(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox_provider.fail_create = True
    response = client.post("/v1/sandboxes", json={}, headers=TENANT)
    assert response.status_code == 502
    assert response.json()["code"] == "sandbox.provider_error"

    # 失败记录保留(status=failed + last_error),不是被事务回滚吞掉
    async with session_scope(client.app.state.session_factory) as session:
        rows = list(await session.scalars(select(SandboxInstance)))
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].last_error


async def test_provider_exec_failure_is_502(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client)
    sandbox_provider.fail_exec = True
    response = client.post(
        f"/v1/sandboxes/{sandbox['id']}/exec",
        json={"argv": ["true"]},
        headers=TENANT,
    )
    assert response.status_code == 502
    assert response.json()["code"] == "sandbox.provider_error"


def test_unavailable_provider_returns_explicit_error(tmp_path: Path) -> None:
    """没有 runtime 的 dev:provider=unavailable 时返回明确 502,不静默成功。"""
    settings = _settings(tmp_path, "unavailable.db", sandbox_provider="unavailable")
    app = create_app(settings, token_verifier=verifier())
    with TestClient(app) as test_client:
        response = test_client.post("/v1/sandboxes", json={}, headers=TENANT)
        assert response.status_code == 502
        assert response.json()["code"] == "sandbox.provider_unavailable"


def test_reaper_task_starts_and_stops(
    tmp_path: Path, sandbox_provider: FakeSandboxProvider
) -> None:
    """reaper 开启时随 lifespan 起后台任务,shutdown 能干净取消。"""
    settings = _settings(tmp_path, "reaper.db", sandbox_reaper_enabled=True)
    app = create_app(
        settings, token_verifier=verifier(), sandbox_provider=sandbox_provider
    )
    with TestClient(app) as test_client:
        assert test_client.get("/healthz").status_code == 200
