"""记忆 REST 端点契约:CRUD/检索/统计/清理、参数校验与跨账号 404。

默认 client fixture 已自足零网络(临时 sqlite + lifespan 建表),记忆接口不依赖
LLM/工具开关;过期清理要先把 expires_at 拨到过去——自建 app 拿到同一个库文件后
直连改库(避免 sleep,也避免测试里依赖时钟)。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from database.base import Base
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient
from sqlalchemy import update

from agent_orchestrator.main import create_app
from agent_orchestrator.models.memory import Memory
from tests.conftest import ACCOUNT, OTHER, _settings, verifier

PREFIX = "/api/v1/memories"


def _create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    """建一条记忆并断言 201;返回响应体。"""
    payload: dict[str, Any] = {"content": "用户偏好简洁回答", **overrides}
    response = client.post(PREFIX, json=payload, headers=ACCOUNT)
    assert response.status_code == 201, response.text
    return response.json()


def _expire_all(database_url: str) -> None:
    """直连同一个库,把所有记忆的过期时间拨到过去。"""

    async def run() -> None:
        factory = create_session_factory(database_url)
        try:
            async with factory.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with session_scope(factory) as session:
                await session.execute(
                    update(Memory).values(
                        expires_at=datetime.now(UTC) - timedelta(days=1)
                    )
                )
        finally:
            await factory.engine.dispose()

    asyncio.run(run())


def test_create_list_search_delete(client: TestClient) -> None:
    memory = _create(client)
    assert memory["scope"] == "account"
    assert memory["kind"] == "note"
    assert memory["pinned"] is False
    assert memory["source_type"] == "manual"
    assert memory["workspace_id"] is None
    # 读模型不泄漏 embedding(存储实现细节)
    assert "embedding" not in memory

    listed = client.get(PREFIX, headers=ACCOUNT).json()
    assert [row["id"] for row in listed] == [memory["id"]]

    search = client.get(
        f"{PREFIX}/search", params={"query": "简洁回答"}, headers=ACCOUNT
    ).json()
    assert search["query"] == "简洁回答"
    assert [hit["id"] for hit in search["hits"]] == [memory["id"]]

    stats = client.get(f"{PREFIX}/stats", headers=ACCOUNT).json()
    assert stats == {"total": 1, "active": 1, "pinned": 0, "expired": 0}

    deleted = client.delete(f"{PREFIX}/{memory['id']}", headers=ACCOUNT)
    assert deleted.status_code == 204
    assert client.get(PREFIX, headers=ACCOUNT).json() == []
    assert client.get(f"{PREFIX}/stats", headers=ACCOUNT).json()["total"] == 0


def test_workspace_kind_and_q_filters(client: TestClient) -> None:
    account_memory = _create(client, content="全账号偏好", kind="preference")
    workspace_one = _create(
        client,
        content="工作区一的事实",
        kind="fact",
        scope="workspace",
        workspace_id="ws-1",
    )
    workspace_two = _create(
        client,
        content="工作区二的事实",
        kind="fact",
        scope="workspace",
        workspace_id="ws-2",
    )

    rows = client.get(PREFIX, params={"workspace_id": "ws-1"}, headers=ACCOUNT).json()
    assert {row["id"] for row in rows} == {
        account_memory["id"],
        workspace_one["id"],
    }

    facts = client.get(PREFIX, params={"kind": "fact"}, headers=ACCOUNT).json()
    assert {row["id"] for row in facts} == {
        workspace_one["id"],
        workspace_two["id"],
    }

    filtered = client.get(PREFIX, params={"q": "全账号"}, headers=ACCOUNT).json()
    assert [row["id"] for row in filtered] == [account_memory["id"]]


def test_validation_and_business_errors(client: TestClient) -> None:
    empty = client.post(PREFIX, json={"content": ""}, headers=ACCOUNT)
    assert empty.status_code == 422
    bad_kind = client.post(
        PREFIX, json={"content": "x", "kind": "unknown"}, headers=ACCOUNT
    )
    assert bad_kind.status_code == 422
    bad_scope = client.post(
        PREFIX, json={"content": "x", "scope": "team"}, headers=ACCOUNT
    )
    assert bad_scope.status_code == 422
    missing_query = client.get(f"{PREFIX}/search", headers=ACCOUNT)
    assert missing_query.status_code == 422
    missing_workspace = client.post(
        PREFIX, json={"content": "x", "scope": "workspace"}, headers=ACCOUNT
    )
    assert missing_workspace.status_code == 400
    assert missing_workspace.json()["code"] == "memory.workspace_required"
    assert client.delete(f"{PREFIX}/nope", headers=ACCOUNT).status_code == 404


def test_account_isolation(client: TestClient) -> None:
    memory = _create(client)
    assert client.get(PREFIX, headers=OTHER).json() == []
    assert (
        client.get(
            f"{PREFIX}/search", params={"query": "简洁回答"}, headers=OTHER
        ).json()["hits"]
        == []
    )
    assert client.get(f"{PREFIX}/stats", headers=OTHER).json() == {
        "total": 0,
        "active": 0,
        "pinned": 0,
        "expired": 0,
    }
    cross_delete = client.delete(f"{PREFIX}/{memory['id']}", headers=OTHER)
    assert cross_delete.status_code == 404
    assert cross_delete.json()["code"] == "memory.not_found"
    assert cross_delete.json()["details"] == {"memory_id": memory["id"]}
    # 跨账号删除失败后,原账号仍能看到自己的记忆
    assert [row["id"] for row in client.get(PREFIX, headers=ACCOUNT).json()] == [
        memory["id"]
    ]


def test_purge_expired_endpoint(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "memory-api.db")
    app = create_app(settings, token_verifier=verifier())
    with TestClient(app) as client:
        _create(client, content="会过期的记忆", ttl_days=1)
        assert client.get(f"{PREFIX}/stats", headers=ACCOUNT).json()["active"] == 1

        _expire_all(settings.database_url)
        stats = client.get(f"{PREFIX}/stats", headers=ACCOUNT).json()
        assert stats == {"total": 1, "active": 0, "pinned": 0, "expired": 1}

        purged = client.post(f"{PREFIX}/purge-expired", headers=ACCOUNT)
        assert purged.json() == {"purged": 1}
        assert client.get(PREFIX, headers=ACCOUNT).json() == []
        # 幂等:再调一次没有可清理的
        again = client.post(f"{PREFIX}/purge-expired", headers=ACCOUNT)
        assert again.json() == {"purged": 0}


def test_create_uses_settings_limits_and_default_ttl(tmp_path: Path) -> None:
    """创建接口接线:条数上限与默认有效期来自 Settings,而不是 hardcode。"""
    settings = _settings(
        tmp_path,
        "memory-api-limits.db",
        memory_max_entries_per_account=1,
        memory_default_ttl_days=2,
    )
    app = create_app(settings, token_verifier=verifier())
    with TestClient(app) as client:
        first = _create(client, content="第一条")
        # 未传 ttl_days:走 settings.memory_default_ttl_days
        assert first["expires_at"] is not None

        second = client.post(PREFIX, json={"content": "第二条"}, headers=ACCOUNT)
        assert second.status_code == 400
        assert second.json()["code"] == "memory.limit_reached"

        client.delete(f"{PREFIX}/{first['id']}", headers=ACCOUNT)
        # 显式 0 表示"不单独设期限",仍按默认有效期而非永久
        third = _create(client, content="第三条", ttl_days=0)
        assert third["expires_at"] is not None
