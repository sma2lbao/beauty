"""Skills(M1):manifest 加载、API、run 绑定与工具白名单。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_orchestrator.services.skills import load_skills
from agent_orchestrator.services.tools import ToolRegistry, ToolSpec
from tests.conftest import TENANT


def test_load_builtin_skills() -> None:
    registry = load_skills()
    ids = [skill.id for skill in registry.list()]
    assert "office.email-summary" in ids
    assert "office.email-weekly-report" in ids
    skill = registry.get("office.email-weekly-report")
    assert skill is not None
    assert "fetch_emails" in skill.tools
    assert skill.system_prompt


def test_skills_api(client: TestClient) -> None:
    listing = client.get("/api/v1/skills", headers=TENANT)
    assert listing.status_code == 200
    ids = [item["id"] for item in listing.json()]
    assert "office.email-summary" in ids

    detail = client.get("/api/v1/skills/office.email-summary", headers=TENANT)
    assert detail.status_code == 200
    assert detail.json()["title"] == "邮件摘要"

    missing = client.get("/api/v1/skills/no.such-skill", headers=TENANT)
    assert missing.status_code == 404
    assert missing.json()["code"] == "skill.not_found"


def test_create_run_binds_skill_snapshot(client: TestClient) -> None:
    response = client.post(
        "/api/v1/runs",
        json={
            "title": "周报",
            "goal": "汇总本周邮件",
            "acceptance_criteria": [],
            "skill_id": "office.email-weekly-report",
        },
        headers=TENANT,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["skill_id"] == "office.email-weekly-report"

    unknown = client.post(
        "/api/v1/runs",
        json={
            "title": "未知技能",
            "goal": "x",
            "acceptance_criteria": [],
            "skill_id": "no.such-skill",
        },
        headers=TENANT,
    )
    assert unknown.status_code == 404


def test_restricted_tool_registry() -> None:
    async def handler(args: dict) -> str:
        return "ok"

    registry = ToolRegistry(
        [
            ToolSpec(name="read", description="read", parameters={}, handler=handler),
            ToolSpec(name="send", description="send", parameters={}, handler=handler),
        ]
    )
    restricted = registry.restricted(["read"])
    assert restricted.names == ["read"]
    assert len(restricted) == 1
    assert len([schema for schema in restricted.schemas()]) == 1


def test_message_can_bind_skill(client: TestClient) -> None:
    conversation = client.post(
        "/api/v1/conversations", json={}, headers=TENANT
    ).json()
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={
            "content": "汇总本周邮件",
            "mode": "task",
            "skill_id": "office.email-weekly-report",
        },
        headers=TENANT,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["run"]["skill_id"] == "office.email-weekly-report"

    missing = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "x", "mode": "task", "skill_id": "no.such-skill"},
        headers=TENANT,
    )
    assert missing.status_code == 404
