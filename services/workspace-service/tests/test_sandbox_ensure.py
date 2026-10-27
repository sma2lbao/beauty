"""P0-05:沙箱 ensure 幂等键(scope_key)与并发收敛。

零网络:provider 用 conftest 的 FakeSandboxProvider;并发用同一 sqlite 文件库上的
两个 session(create_session_factory 是 NullPool,每个 session 独立 aiosqlite 连接)。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from database.session import session_scope
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.conftest import OTHER, TENANT, FakeSandboxProvider
from workspace.core.config import Settings
from workspace.models.sandbox import SandboxInstance
from workspace.schemas.sandbox import SandboxCreate
from workspace.services import sandbox as sandbox_service

ENSURE_URL = "/v1/sandboxes:ensure"


def _post_ensure(
    client: TestClient,
    scope_key: str,
    *,
    headers: dict[str, str] | None = None,
    **payload: Any,
) -> tuple[dict[str, Any], int]:
    response = client.post(
        ENSURE_URL,
        json={"scope_key": scope_key, **payload},
        headers=TENANT if headers is None else headers,
    )
    assert response.status_code in (200, 201), response.text
    return response.json(), response.status_code


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


async def _ensure_in_session(
    client: TestClient,
    provider: FakeSandboxProvider,
    *,
    tenant_id: str,
    scope_key: str,
) -> tuple[SandboxInstance, bool]:
    """绕过 HTTP 直接走服务层,便于在事件循环里并发调用。"""
    factory = client.app.state.session_factory
    settings: Settings = client.app.state.settings
    async with session_scope(factory) as session:
        return await sandbox_service.ensure_sandbox(
            session,
            tenant_id,
            scope_key=scope_key,
            spec=SandboxCreate(),
            provider=provider,
            settings=settings,
        )


async def _count_scope_rows(client: TestClient, scope_key: str) -> int:
    factory = client.app.state.session_factory
    async with session_scope(factory) as session:
        count = await session.scalar(
            select(func.count())
            .select_from(SandboxInstance)
            .where(SandboxInstance.scope_key == scope_key)
        )
    return int(count or 0)


def test_ensure_is_idempotent(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    first, first_status = _post_ensure(client, "conv:c-1", ttl_seconds=600)
    assert first_status == 201
    assert first["created"] is True
    sandbox = first["sandbox"]
    assert sandbox["scope_key"] == "conv:c-1"
    assert sandbox["status"] == "running"
    assert sandbox["provider"] == "fake"
    assert len(sandbox_provider.created) == 1

    second, second_status = _post_ensure(client, "conv:c-1")
    assert second_status == 200
    assert second["created"] is False
    assert second["sandbox"]["id"] == sandbox["id"]
    assert second["sandbox"]["scope_key"] == "conv:c-1"
    assert len(sandbox_provider.created) == 1
    # 复用会滑动续期
    assert _parse_utc(second["sandbox"]["expires_at"]) >= _parse_utc(
        sandbox["expires_at"]
    )


def test_ensure_detail_and_list_expose_scope_key(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    created, _ = _post_ensure(client, "run:r-1")
    sandbox_id = created["sandbox"]["id"]

    detail = client.get(f"/v1/sandboxes/{sandbox_id}", headers=TENANT)
    assert detail.status_code == 200
    assert detail.json()["scope_key"] == "run:r-1"

    listed = client.get("/v1/sandboxes", headers=TENANT)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [sandbox_id]
    assert listed.json()[0]["scope_key"] == "run:r-1"


def test_list_filters_by_scope_key(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    """执行现场按会话定位:GET /v1/sandboxes?scope_key=... 精确命中。"""
    first, _ = _post_ensure(client, "conv:c-1")
    second, _ = _post_ensure(client, "run:r-1")
    plain = client.post("/v1/sandboxes", json={}, headers=TENANT).json()

    hit = client.get("/v1/sandboxes", params={"scope_key": "conv:c-1"}, headers=TENANT)
    assert hit.status_code == 200
    assert [item["id"] for item in hit.json()] == [first["sandbox"]["id"]]

    miss = client.get(
        "/v1/sandboxes", params={"scope_key": "conv:none"}, headers=TENANT
    )
    assert miss.status_code == 200
    assert miss.json() == []

    # 过滤只收窄、不改既有排序与租户范围
    all_ids = {
        item["id"] for item in client.get("/v1/sandboxes", headers=TENANT).json()
    }
    assert all_ids == {
        first["sandbox"]["id"],
        second["sandbox"]["id"],
        plain["id"],
    }
    assert (
        client.get(
            "/v1/sandboxes", params={"scope_key": "conv:c-1"}, headers=OTHER
        ).json()
        == []
    )


def test_plain_create_keeps_scope_key_empty(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    created = client.post("/v1/sandboxes", json={}, headers=TENANT)
    assert created.status_code == 201
    assert created.json()["scope_key"] is None
    listed = client.get("/v1/sandboxes", headers=TENANT).json()
    assert [item["scope_key"] for item in listed] == [None]


def test_ensure_tenant_isolation(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    """同一 scope_key 在不同租户下互不影响,也互不可见。"""
    tenant_record, tenant_status = _post_ensure(client, "conv:same", headers=TENANT)
    other_record, other_status = _post_ensure(client, "conv:same", headers=OTHER)
    assert tenant_status == 201
    assert other_status == 201
    assert tenant_record["sandbox"]["id"] != other_record["sandbox"]["id"]
    assert tenant_record["sandbox"]["scope_key"] == "conv:same"
    assert other_record["sandbox"]["scope_key"] == "conv:same"
    assert len(sandbox_provider.created) == 2

    other_id = other_record["sandbox"]["id"]
    assert client.get(f"/v1/sandboxes/{other_id}", headers=TENANT).status_code == 404


async def test_ensure_rebuilds_expired_sandbox(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    created, _ = _post_ensure(client, "conv:c-2")
    sandbox = created["sandbox"]
    await _set_expired(client, sandbox["id"])

    rebuilt, status_code = _post_ensure(client, "conv:c-2")
    assert status_code == 201
    assert rebuilt["created"] is True
    assert rebuilt["sandbox"]["id"] == sandbox["id"]
    assert rebuilt["sandbox"]["name"] != sandbox["name"]
    assert rebuilt["sandbox"]["scope_key"] == "conv:c-2"
    assert rebuilt["sandbox"]["status"] == "running"
    # 旧运行时被回收,新运行时用新 name 拉起
    assert sandbox_provider.destroyed == [sandbox["name"]]
    assert [spec.name for spec in sandbox_provider.created] == [
        sandbox["name"],
        rebuilt["sandbox"]["name"],
    ]


async def test_ensure_retries_after_provider_failure(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    """provider 失败留下 failed 行(占用 scope_key),下次 ensure 原地重建。"""
    sandbox_provider.fail_create = True
    failed = client.post(ENSURE_URL, json={"scope_key": "conv:c-3"}, headers=TENANT)
    assert failed.status_code == 502
    assert failed.json()["code"] == "sandbox.provider_error"
    assert await _count_scope_rows(client, "conv:c-3") == 1

    sandbox_provider.fail_create = False
    rebuilt, status_code = _post_ensure(client, "conv:c-3")
    assert status_code == 201
    assert rebuilt["created"] is True
    assert rebuilt["sandbox"]["status"] == "running"
    assert rebuilt["sandbox"]["last_error"] is None


async def test_ensure_concurrent_single_row(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    """并发 gather ×2:唯一约束兜底,只落一行、只拉起一个运行时。"""
    results = await asyncio.gather(
        *(
            _ensure_in_session(
                client, sandbox_provider, tenant_id="t-1", scope_key="conv:race"
            )
            for _ in range(2)
        )
    )
    ids = {record.id for record, _ in results}
    assert len(ids) == 1
    assert sorted(created for _, created in results) == [False, True]
    assert len(sandbox_provider.created) == 1
    assert await _count_scope_rows(client, "conv:race") == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"scope_key": ""},
        {"scope_key": "Conv:1"},
        {"scope_key": "conv 1"},
        {"scope_key": "conv:" + "x" * 95},
    ],
)
def test_ensure_rejects_invalid_scope_key(
    client: TestClient, payload: dict[str, Any]
) -> None:
    response = client.post(ENSURE_URL, json=payload, headers=TENANT)
    assert response.status_code == 422
