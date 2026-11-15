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
from agent_orchestrator.models.memory import Memory, MemoryReview
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
    # GET 只读:预览检索不能污染访问统计(缓存/审计网关会重复 GET)
    assert search["hits"][0]["access_count"] == 0

    touched = client.post(
        f"{PREFIX}/search", json={"query": "简洁回答"}, headers=ACCOUNT
    )
    assert touched.status_code == 200
    assert [hit["id"] for hit in touched.json()["hits"]] == [memory["id"]]
    assert client.get(PREFIX, headers=ACCOUNT).json()[0]["access_count"] == 1

    stats = client.get(f"{PREFIX}/stats", headers=ACCOUNT).json()
    assert stats == {
        "total": 1,
        "active": 1,
        "pinned": 0,
        "expired": 0,
        "superseded": 0,
        "pending_reviews": 0,
    }

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
    empty_query = client.post(f"{PREFIX}/search", json={"query": ""}, headers=ACCOUNT)
    assert empty_query.status_code == 422
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
        "superseded": 0,
        "pending_reviews": 0,
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
        assert stats == {
            "total": 1,
            "active": 0,
            "pinned": 0,
            "expired": 1,
            "superseded": 0,
            "pending_reviews": 0,
        }

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
        # 显式 0 表示永久,不再被部署默认值覆盖
        third = _create(client, content="第三条", ttl_days=0)
        assert third["expires_at"] is None


def _insert_review(
    database_url: str,
    *,
    content: str,
    kind: str = "fact",
    confidence: float = 0.3,
) -> str:
    """直连库插一条待复核候选(复核队列只有自动抽取会写,API 不提供入口)。"""

    async def run() -> str:
        factory = create_session_factory(database_url)
        try:
            async with factory.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with session_scope(factory) as session:
                review = MemoryReview(
                    account_id="t-1",
                    scope="account",
                    kind=kind,
                    content=content,
                    confidence=confidence,
                    evidence_quote="用户原话",
                    source_type="chat",
                    source_id="c-1",
                )
                session.add(review)
                await session.flush()
                return review.id
        finally:
            await factory.engine.dispose()

    return asyncio.run(run())


def test_get_patch_and_optimistic_conflict(client: TestClient) -> None:
    memory = _create(client)
    fetched = client.get(f"{PREFIX}/{memory['id']}", headers=ACCOUNT)
    assert fetched.status_code == 200
    assert fetched.json()["content"] == memory["content"]

    patched = client.patch(
        f"{PREFIX}/{memory['id']}",
        json={
            "content": "用户偏好一句话回答",
            "pinned": True,
            "expected_updated_at": memory["updated_at"],
        },
        headers=ACCOUNT,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["content"] == "用户偏好一句话回答"
    assert patched.json()["pinned"] is True

    # 旧的 updated_at 再提交 = 乐观并发冲突
    stale = client.patch(
        f"{PREFIX}/{memory['id']}",
        json={"content": "另一次修改", "expected_updated_at": memory["updated_at"]},
        headers=ACCOUNT,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "memory.conflict"

    # ttl_days=0 显式改永久
    permanent = client.patch(
        f"{PREFIX}/{memory['id']}", json={"ttl_days": 0}, headers=ACCOUNT
    )
    assert permanent.status_code == 200
    assert permanent.json()["expires_at"] is None
    assert client.get(f"{PREFIX}/nope", headers=ACCOUNT).status_code == 404


def test_sensitive_content_rejected(client: TestClient) -> None:
    rejected = client.post(
        PREFIX,
        json={"content": "部署私钥 -----BEGIN RSA PRIVATE KEY----- 请记住"},
        headers=ACCOUNT,
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "memory.sensitive_rejected"
    assert client.get(PREFIX, headers=ACCOUNT).json() == []


def test_new_fact_supersedes_old_and_include_invalid(client: TestClient) -> None:
    old = _create(
        client,
        content="用户偏好使用中文回复并保持简洁",
        kind="preference",
    )
    new = _create(
        client,
        content="用户偏好使用英文回复并保持简洁",
        kind="preference",
    )
    active = client.get(PREFIX, headers=ACCOUNT).json()
    assert [row["id"] for row in active] == [new["id"]]

    history = client.get(
        PREFIX, params={"include_invalid": True}, headers=ACCOUNT
    ).json()
    assert {row["id"] for row in history} == {old["id"], new["id"]}
    stale = next(row for row in history if row["id"] == old["id"])
    assert stale["invalid_at"] is not None
    assert stale["superseded_by"] == new["id"]
    assert client.get(f"{PREFIX}/stats", headers=ACCOUNT).json()["superseded"] == 1


def test_review_queue_approve_and_reject(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "memory-reviews.db")
    app = create_app(settings, token_verifier=verifier())
    with TestClient(app) as client:
        review_id = _insert_review(settings.database_url, content="用户下周去东京")
        listed = client.get(f"{PREFIX}/reviews", headers=ACCOUNT).json()
        assert [item["id"] for item in listed] == [review_id]
        assert listed[0]["status"] == "pending"
        assert listed[0]["confidence"] == 0.3

        approved = client.post(
            f"{PREFIX}/reviews/{review_id}/approve",
            json={"note": "确认"},
            headers=ACCOUNT,
        )
        assert approved.status_code == 201, approved.text
        assert approved.json()["content"] == "用户下周去东京"
        assert client.get(f"{PREFIX}/reviews", headers=ACCOUNT).json() == []
        assert client.get(PREFIX, headers=ACCOUNT).json()[0]["source_type"] == "manual"

        # 已处理的复核项再次提交按冲突处理
        again = client.post(
            f"{PREFIX}/reviews/{review_id}/approve", json={}, headers=ACCOUNT
        )
        assert again.status_code == 409
        assert again.json()["code"] == "memory.review_resolved"

        rejected_id = _insert_review(settings.database_url, content="用户讨厌表格")
        rejected = client.post(
            f"{PREFIX}/reviews/{rejected_id}/reject", json={}, headers=ACCOUNT
        )
        assert rejected.status_code == 204
        assert client.get(f"{PREFIX}/reviews", headers=ACCOUNT).json() == []
        assert len(client.get(PREFIX, headers=ACCOUNT).json()) == 1


def test_export_and_purge_all(client: TestClient) -> None:
    _create(client)
    exported = client.get(f"{PREFIX}/export", headers=ACCOUNT)
    assert exported.status_code == 200
    payload = exported.json()
    assert len(payload["memories"]) == 1
    assert "exported_at" in payload

    unconfirmed = client.delete(PREFIX, headers=ACCOUNT)
    assert unconfirmed.status_code == 400
    assert unconfirmed.json()["code"] == "memory.confirm_required"

    purged = client.delete(f"{PREFIX}?confirm=true", headers=ACCOUNT)
    assert purged.status_code == 200
    assert purged.json() == {"memories": 1, "reviews": 0}
    assert client.get(PREFIX, headers=ACCOUNT).json() == []
