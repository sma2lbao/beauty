"""计划审批自动放行(§6.2):无 DANGER 工具不再每跑必审。

判据 = run 实际可用的工具面(基础工具 + 引用的 MCP,按技能 tools 白名单裁剪)
里有没有 DANGER 级工具:

- 没有 → 图不进 interrupt,plan 一次跑到终态,事件补记 planned(auto_approved)
  与 approved(auto);
- 有 → 仍停在 awaiting_approval,等人工放行(确认闸行为不变)。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.graph.nodes import make_approval_gate_node
from agent_orchestrator.main import create_app
from agent_orchestrator.services.tools import ToolRegistry
from tests.conftest import (
    TENANT,
    FakeDangerTool,
    FakeLLM,
    FakeWorkspace,
    _create_run,
    _settings,
    verifier,
)

PLAN = "# 执行计划\n\n## 步骤\n1. 整理日程"
EXECUTE = "已整理本周日程"
VERIFY = '{"summary": "全部通过", "verdicts": {"日程已整理": true}}'
DELIVER = "# 本周周报\n\n正文"


def _event_types(client: TestClient, run_id: str) -> list[str]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT)
    assert resp.status_code == 200
    return [event["type"] for event in resp.json()]


def _events(client: TestClient, run_id: str) -> list[dict]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT)
    assert resp.status_code == 200
    return resp.json()


def test_plan_auto_approves_without_dangerous_tools(tmp_path: Path) -> None:
    fake = FakeLLM([PLAN, EXECUTE, VERIFY, DELIVER])
    app = create_app(
        _settings(tmp_path, "auto.db", llm_enabled=True, plan_auto_approve=True),
        llm_client=fake,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        resp = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        assert resp.status_code == 200
        body = resp.json()
        # 一次跑到终态,不再停在 awaiting_approval
        assert body["status"] == "completed"
        assert body["plan_markdown"] == PLAN

        events = _events(client, run_id)
        types = [event["type"] for event in events]
        lifecycle = [t for t in types if t != "run.item.v1"]
        assert lifecycle[:3] == [
            "run.created.v1",
            "run.planned.v1",
            "run.approved.v1",
        ]
        assert "run.completed.v1" in types
        planned = next(e for e in events if e["type"] == "run.planned.v1")
        assert planned["payload"] == {"attempt": 1, "auto_approved": True}
        approved = next(e for e in events if e["type"] == "run.approved.v1")
        assert approved["payload"] == {"attempt": 1, "auto": True}

        # 交付物仍按统一路径落库(自动放行不影响交付)
        artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=TENANT)
        assert artifacts.status_code == 200
        rows = artifacts.json()
        assert len(rows) == 1
        assert rows[0]["source"] == "report"

        # 已终态:不能再人工放行
        assert (
            client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).status_code
            == 409
        )


def test_plan_still_waits_when_dangerous_tool_available(tmp_path: Path) -> None:
    danger = FakeDangerTool()
    fake = FakeLLM([PLAN, EXECUTE, VERIFY, DELIVER])
    app = create_app(
        _settings(
            tmp_path,
            "danger.db",
            llm_enabled=True,
            tools_enabled=True,
            plan_auto_approve=True,
        ),
        llm_client=fake,
        token_verifier=verifier(),
        toolkit_factory=lambda tenant: ToolRegistry([danger.spec()]),
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        body = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).json()
        assert body["status"] == "awaiting_approval"
        types = _event_types(client, run_id)
        assert "run.approved.v1" not in types

        done = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert done["status"] == "completed"
        assert "run.approved.v1" in _event_types(client, run_id)
        # 危险工具没有被调用(模型没请求它;放行只针对计划)
        assert danger.calls == []


def test_plan_auto_approve_switch_off_keeps_manual_gate(tmp_path: Path) -> None:
    fake = FakeLLM([PLAN, EXECUTE, VERIFY, DELIVER])
    app = create_app(
        _settings(tmp_path, "manual.db", llm_enabled=True, plan_auto_approve=False),
        llm_client=fake,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        body = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).json()
        assert body["status"] == "awaiting_approval"
        assert [t for t in _event_types(client, run_id) if t != "run.item.v1"] == [
            "run.created.v1",
            "run.planned.v1",
        ]


async def test_skill_whitelist_narrows_away_dangerous_tools() -> None:
    """白名单裁掉危险工具后,该 run 的工具面没有 DANGER → 自动放行。"""
    node = make_approval_gate_node(
        lambda tenant: ToolRegistry([FakeDangerTool().spec()]), None
    )
    result = await node(
        {
            "tenant_id": "t-1",
            "allowed_tools": ["search_knowledge"],
            "plan_markdown": PLAN,
        }
    )
    assert result == {"auto_approved": True}


async def test_empty_whitelist_exposes_no_tools_and_auto_approves() -> None:
    """技能 tools=[] 表示不用任何工具:与 execute 同口径,没有 DANGER 可做 → 放行。"""
    node = make_approval_gate_node(
        lambda tenant: ToolRegistry([FakeDangerTool().spec()]), None
    )
    result = await node(
        {"tenant_id": "t-1", "allowed_tools": [], "plan_markdown": PLAN}
    )
    assert result == {"auto_approved": True}


def test_stream_task_auto_approves_and_mirrors_artifacts(tmp_path: Path) -> None:
    """SSE 任务路径也必须把交付依赖传下去(否则自动放行的 run 丢工作区镜像)。"""
    # 对话创建的 run 没有显式验收标准 → verify 直接通过,只调 plan/execute/deliver
    fake = FakeLLM([PLAN, EXECUTE, DELIVER])
    workspace = FakeWorkspace()
    app = create_app(
        _settings(
            tmp_path,
            "auto-stream.db",
            llm_enabled=True,
            plan_auto_approve=True,
        ),
        llm_client=fake,
        token_verifier=verifier(),
        workspace_client=workspace,
    )
    with TestClient(app) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=TENANT
        ).json()["id"]
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"content": "把本周日程整理成周报", "mode": "task"},
            headers=TENANT,
        )
        assert resp.status_code == 200

        runs = client.get("/api/v1/runs?limit=5", headers=TENANT).json()
        assert len(runs) == 1
        run = runs[0]
        assert run["status"] == "completed"
        events = _events(client, run["id"])
        types = [event["type"] for event in events]
        planned = next(e for e in events if e["type"] == "run.planned.v1")
        assert planned["payload"] == {"attempt": 1, "auto_approved": True}
        assert "run.approved.v1" in types

        # 交付物照常落库 + 镜像到工作区:两处依赖都最终到了 delivery
        artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts", headers=TENANT)
        assert [row["source"] for row in artifacts.json()] == ["report"]
        assert len(workspace.created) == 1
        assert workspace.created[0]["content"] == DELIVER
