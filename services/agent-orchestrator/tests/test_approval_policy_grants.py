"""P0-4 审批策略服务层:会话 grant 合并、替换不动 grant、授权落库。"""

from __future__ import annotations

import asyncio

from database.session import create_session_factory, session_scope

from agent_orchestrator.repositories import ConversationApprovalGrantRepository
from agent_orchestrator.services import approval as approval_service
from tests.conftest import ACCOUNT, FakeLLM, _settings, _tool_client

# ---- 服务层:grant 合并 / 替换不动 grant / 授权落库 ----


def _db_url(tmp_path) -> str:
    return _settings(
        tmp_path, "tools.db", llm_enabled=True, tools_enabled=True
    ).database_url


def test_load_policies_merges_conversation_grants(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        client.put(
            "/api/v1/approval-policies",
            json={"policies": [{"tool": "zz_tool", "action": "deny"}]},
            headers=ACCOUNT,
        )
        url = _db_url(tmp_path)

        async def seeded() -> list[dict[str, str]]:
            factory = create_session_factory(url)
            try:
                async with session_scope(factory) as session:
                    grant_repo = ConversationApprovalGrantRepository(session)
                    effective = await approval_service.record_grant(
                        session,
                        account_id="t-1",
                        conversation_id=conversation_id,
                        tool="aa_tool",
                        grant="session",
                    )
                    assert effective == "session"
                    assert await grant_repo.list_for_conversation(
                        "t-1", conversation_id
                    )
                    return await approval_service.load_policies(
                        session, "t-1", conversation_id
                    )
            finally:
                await factory.engine.dispose()

        rows = asyncio.run(seeded())
        # 账号行在前(按工具排序)、grant 行在后
        assert [(row["tool"], row["origin"]) for row in rows] == [
            ("zz_tool", "account"),
            ("aa_tool", "grant"),
        ]
        assert rows[1]["action"] == "auto"

        # PUT 整表替换只动账号行:grant 不受影响
        client.put("/api/v1/approval-policies", json={"policies": []}, headers=ACCOUNT)

        async def after_replace() -> list[dict[str, str]]:
            factory = create_session_factory(url)
            try:
                async with session_scope(factory) as session:
                    return await approval_service.load_policies(
                        session, "t-1", conversation_id
                    )
            finally:
                await factory.engine.dispose()

        rows = asyncio.run(after_replace())
        assert [row["origin"] for row in rows] == ["grant"]


def test_record_grant_session_without_conversation_degrades(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])):
        url = _db_url(tmp_path)

        async def degrade() -> list[dict[str, str]]:
            factory = create_session_factory(url)
            try:
                async with session_scope(factory) as session:
                    effective = await approval_service.record_grant(
                        session,
                        account_id="t-1",
                        conversation_id=None,
                        tool="notes__create",
                        grant="session",
                    )
                    assert effective == "once"
                    return await approval_service.load_policies(session, "t-1")
            finally:
                await factory.engine.dispose()

        assert asyncio.run(degrade()) == []


def test_record_grant_always_writes_account_auto_policy(tmp_path) -> None:
    with _tool_client(tmp_path, FakeLLM([])) as client:
        url = _db_url(tmp_path)

        async def record(grant: str) -> str:
            factory = create_session_factory(url)
            try:
                async with session_scope(factory) as session:
                    return await approval_service.record_grant(
                        session,
                        account_id="t-1",
                        conversation_id=None,
                        tool="external_publish",
                        grant=grant,
                    )
            finally:
                await factory.engine.dispose()

        assert asyncio.run(record("once")) == "once"
        assert client.get("/api/v1/approval-policies", headers=ACCOUNT).json() == []
        assert asyncio.run(record("always")) == "always"
        rows = client.get("/api/v1/approval-policies", headers=ACCOUNT).json()
        assert rows == [
            {
                "tool": "external_publish",
                "action": "auto",
                "arg_name": "",
                "arg_match": "",
                "arg_value": "",
            }
        ]
