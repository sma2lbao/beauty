"""main 的 extras factory 与图路径端到端:skill_run 只在 run 绑定包时暴露。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.skill_runner import UNPACK_PATH
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import TENANT, FakeLLM, FakeWorkspace, _settings, verifier
from tests.support.skill_runner import FakeSandbox, plain_package


class HashingWorkspace(FakeWorkspace):
    """put_object 返回真实 sha256(与 run 快照一致),端到端执行要用。"""

    async def put_object(
        self,
        tenant_id: str,
        data: bytes,
        *,
        filename: str | None = None,
        content_type: str = "application/zip",
    ) -> dict[str, Any]:
        self.sha256 = hashlib.sha256(data).hexdigest()
        return await super().put_object(
            tenant_id, data, filename=filename, content_type=content_type
        )


def _upload_package(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/skills/packages",
        files={"file": ("weekly.zip", plain_package(), "application/zip")},
        headers=TENANT,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_run(client: TestClient, **payload: Any) -> dict[str, Any]:
    response = client.post(
        "/api/v1/runs",
        json={"title": "周报", "goal": "生成周报", **payload},
        headers=TENANT,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _extras(client: TestClient, ctx: ToolContext) -> list[Any]:
    factory = client.app.state.tool_extras_factory
    with bind_tool_context(ctx):
        return asyncio.run(factory(ctx.tenant_id))


@pytest.fixture()
def skill_client(tmp_path: Path) -> Iterator[TestClient]:
    workspace = HashingWorkspace()
    sandbox = FakeSandbox()
    app = create_app(
        _settings(
            tmp_path, "extras.db", skill_execution_enabled=True, mcp_enabled=False
        ),
        workspace_client=workspace,
        sandbox_client=sandbox,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        yield client


def test_extras_exposes_skill_run_for_package_run(skill_client: TestClient) -> None:
    _upload_package(skill_client)
    run = _create_run(skill_client, skill_id="team.weekly-report")
    specs = _extras(skill_client, ToolContext(tenant_id="t-1", run_id=run["id"]))
    assert [spec.name for spec in specs] == ["skill_run"]
    assert specs[0].risk is ToolRisk.WRITE
    assert specs[0].requires_confirmation is False


def test_extras_skips_skill_run_without_package(skill_client: TestClient) -> None:
    run = _create_run(skill_client)
    assert _extras(skill_client, ToolContext(tenant_id="t-1", run_id=run["id"])) == []


def test_extras_has_no_skill_run_in_chat_path(skill_client: TestClient) -> None:
    _upload_package(skill_client)
    _create_run(skill_client, skill_id="team.weekly-report")
    ctx = ToolContext(tenant_id="t-1", conversation_id="conv-1")
    assert _extras(skill_client, ctx) == []


def test_extras_unknown_run_id_has_no_skill_run(skill_client: TestClient) -> None:
    _upload_package(skill_client)
    ctx = ToolContext(tenant_id="t-1", run_id="no-such-run")
    assert _extras(skill_client, ctx) == []


def test_extras_mcp_failure_does_not_block_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> list[Any]:
        raise RuntimeError("MCP 库挂了")

    monkeypatch.setattr(mcp_service, "tool_specs", boom)
    workspace = HashingWorkspace()
    app = create_app(
        _settings(tmp_path, "extras-mcp.db", skill_execution_enabled=True),
        workspace_client=workspace,
        sandbox_client=FakeSandbox(),
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        _upload_package(client)
        run = _create_run(client, skill_id="team.weekly-report")
        specs = _extras(client, ToolContext(tenant_id="t-1", run_id=run["id"]))
    assert [spec.name for spec in specs] == ["skill_run"]


def test_runner_not_assembled_without_workspace(tmp_path: Path) -> None:
    app = create_app(
        _settings(tmp_path, "no-workspace.db", skill_execution_enabled=True),
        sandbox_client=FakeSandbox(),
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        assert client.app.state.skill_runner is None


# ---------------------------------------------------------------------------
# 图路径端到端:skill_run 属受控写,approve 后自动执行
# ---------------------------------------------------------------------------


def test_graph_executes_skill_run_without_confirmation(tmp_path: Path) -> None:
    """skill_run 是受控写(WRITE):approve 后直接执行,不再弹确认闸(§6.2)。"""
    verify_pass = '{"summary": "已生成", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(
        [
            "# 计划",
            LLMReply(
                content="",
                tool_calls=(
                    ToolCall(
                        id="call_skill",
                        name="skill_run",
                        arguments='{"input": "本周日程"}',
                    ),
                ),
            ),
            "技能执行完成",
            verify_pass,
            "# 最终交付正文",
        ]
    )
    workspace = HashingWorkspace()
    sandbox = FakeSandbox()
    # 空产物目录:避免图事务持锁期间写产物(登记路径已由上面的单元用例覆盖)
    sandbox.listing = {"files": [], "truncated": False}
    app = create_app(
        _settings(
            tmp_path,
            "graph-skill.db",
            llm_enabled=True,
            skill_execution_enabled=True,
            mcp_enabled=False,
        ),
        llm_client=fake,
        workspace_client=workspace,
        sandbox_client=sandbox,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        assert client.app.state.skill_runner is not None
        _upload_package(client)
        run = _create_run(
            client,
            skill_id="team.weekly-report",
            acceptance_criteria=["日程已整理"],
        )

        planned = client.post(f"/api/v1/runs/{run['id']}/plan", headers=TENANT)
        assert planned.status_code == 200, planned.text
        assert sandbox.execs == []  # 审批前不执行上传代码

        done = client.post(f"/api/v1/runs/{run['id']}/approve", headers=TENANT)
        assert done.status_code == 200, done.text
        # 受控写自动执行:不再停在 awaiting_confirmation,直接跑完
        assert done.json()["status"] == "completed"

        # 沙箱生命周期:解包 → entrypoint → 列产物 → 销毁
        assert [item["argv"][1] for item in sandbox.execs][0] == UNPACK_PATH
        assert sandbox.destroyed == ["sb-1"]
        assert json.loads(sandbox.execs[1]["stdin"])["input"] == "本周日程"

        # execute 的 function-calling schema 里能看到 skill_run
        schema_names = [
            tools[0]["function"]["name"] for tools in fake.tools_seen if tools
        ]
        assert "skill_run" in schema_names

        artifact = client.get(
            f"/api/v1/runs/{run['id']}/artifact", headers=TENANT
        ).json()
        assert artifact["content"] == "# 最终交付正文"

        events = client.get(f"/api/v1/runs/{run['id']}/events", headers=TENANT).json()
        types = [event["type"] for event in events]
        # 没有确认闸事件:受控写不打断
        assert "run.confirmation.required.v1" not in types
        assert "run.confirmation.received.v1" not in types

        tool_events = [
            event for event in events if event["type"] == "run.tool.called.v1"
        ]
        assert [event["payload"]["name"] for event in tool_events] == ["skill_run"]
        assert tool_events[0]["payload"]["ok"] is True
