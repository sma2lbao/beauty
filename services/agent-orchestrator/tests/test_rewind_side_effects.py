"""rewind/fork 的外部副作用台账(S5,见 docs/design/recovery.md §6/§10)。

回退会重放 checkpoint 之后的节点:同一参数同工具的重复调用由稳定幂等键按重放
返回,但参数变了就是新的外部写,fork 换 run_id 后更是全新的键。所以先给台账
(GET /side-effects),再要显式确认(acknowledge_side_effects),否则 409。

台账来源直接注入 run.tool.called.v1 事件(等价于"工具已经调用过"),不依赖工具
服务与沙箱。
"""

from __future__ import annotations

import asyncio

from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories.constants import TOOL_CALLED_EVENT
from agent_orchestrator.services.run_events import append_event
from tests.conftest import (
    ACCOUNT,
    FakeLLM,
    _client,
    _create_run,
    _settings,
)

VERIFY_PASS = '{"summary": "ok", "verdicts": {"日程已整理": true}}'


def _four_replies() -> list[str]:
    return ["# 计划", "执行", VERIFY_PASS, "# 交付物"]


def _tool_payload(
    name: str,
    *,
    risk: str = "write",
    ok: bool = True,
    tool_call_id: str = "call-1",
    arguments: str = "{}",
) -> dict:
    return {
        "name": name,
        "arguments": arguments,
        "ok": ok,
        "detail": "done",
        "tool_call_id": tool_call_id,
        "risk": risk,
        "decision": "auto",
        "error_code": None,
        "duration_ms": 3,
        "result_chars": 12,
        "truncated": False,
        "paths": [],
        "sha256_before": None,
        "sha256_after": None,
        "offloaded": False,
        "offload_path": None,
        "original_chars": None,
    }


def _append_tool_events(settings: Settings, run_id: str, payloads: list[dict]) -> None:
    """把工具调用事件直接写进库(与图执行后的落点同构)。"""

    async def scenario() -> None:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                run = await session.get(Run, run_id)
                assert run is not None
                for payload in payloads:
                    await append_event(session, run, TOOL_CALLED_EVENT, payload)
        finally:
            await factory.engine.dispose()

    asyncio.run(scenario())


def _complete_run(client: TestClient) -> str:
    run_id = _create_run(client)
    planned = client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
    assert planned.status_code == 200
    approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
    assert approved.json()["status"] == "completed"
    return run_id


def _plan_checkpoint(client: TestClient, run_id: str) -> str:
    rows = client.get(f"/api/v1/runs/{run_id}/checkpoints", headers=ACCOUNT).json()
    return next(
        row["checkpoint_id"]
        for row in rows
        if row["next"] == ["approval_gate"] and row["summary"]["plan_chars"] > 0
    )


def _events(client: TestClient, run_id: str) -> list[dict]:
    return client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()


def _rewind_event(client: TestClient, run_id: str) -> dict:
    events = _events(client, run_id)
    return next(event for event in events if event["type"] == "run.rewind.v1")


def test_ledger_lists_only_write_and_danger(tmp_path) -> None:
    settings = _settings(tmp_path, "llm.db", llm_enabled=True)
    with _client(tmp_path, FakeLLM(_four_replies())) as client:
        run_id = _complete_run(client)
        _append_tool_events(
            settings,
            run_id,
            [
                _tool_payload("search", risk="read", tool_call_id="r1"),
                _tool_payload("write_file", risk="write", tool_call_id="w1"),
                _tool_payload(
                    "notion_update",
                    risk="danger",
                    ok=False,
                    tool_call_id="d1",
                    arguments='{"page": "x"}',
                ),
            ],
        )
        resp = client.get(f"/api/v1/runs/{run_id}/side-effects", headers=ACCOUNT)
        assert resp.status_code == 200
        effects = resp.json()
        assert [effect["name"] for effect in effects] == ["write_file", "notion_update"]
        assert [effect["risk"] for effect in effects] == ["write", "danger"]
        assert [effect["ok"] for effect in effects] == [True, False]
        assert effects[1]["tool_call_id"] == "d1"
        assert effects[1]["arguments"] == '{"page": "x"}'


def test_rewind_requires_acknowledgement_when_writes_happened(tmp_path) -> None:
    settings = _settings(tmp_path, "llm.db", llm_enabled=True)
    fake = FakeLLM(_four_replies() + ["重跑执行", VERIFY_PASS, "# 重跑交付"])
    with _client(tmp_path, fake) as client:
        run_id = _complete_run(client)
        checkpoint_id = _plan_checkpoint(client, run_id)
        _append_tool_events(
            settings, run_id, [_tool_payload("write_file", tool_call_id="w1")]
        )

        blocked = client.post(
            f"/api/v1/runs/{run_id}/rewind",
            json={"checkpoint_id": checkpoint_id, "reason": "改需求"},
            headers=ACCOUNT,
        )
        assert blocked.status_code == 409
        body = blocked.json()
        assert body["code"] == "run.side_effects_present"
        assert body["details"]["action"] == "rewind"
        assert body["details"]["count"] == 1
        assert body["details"]["tools"] == ["write_file"]
        assert body["details"]["side_effects"] == [
            {
                "seq": body["details"]["side_effects"][0]["seq"],
                "tool_call_id": "w1",
                "name": "write_file",
                "risk": "write",
                "ok": True,
            }
        ]
        # 拒绝不改变状态、不重跑图
        current = client.get(f"/api/v1/runs/{run_id}", headers=ACCOUNT).json()
        assert current["status"] == "completed"
        assert len(fake.calls) == 4

        allowed = client.post(
            f"/api/v1/runs/{run_id}/rewind",
            json={
                "checkpoint_id": checkpoint_id,
                "reason": "改需求",
                "acknowledge_side_effects": True,
            },
            headers=ACCOUNT,
        )
        assert allowed.status_code == 200
        assert allowed.json()["status"] == "awaiting_approval"
        rewound = _rewind_event(client, run_id)
        summary = rewound["payload"]["side_effects"]
        assert summary == {"count": 1, "tools": ["write_file"]}


def test_rewind_without_side_effects_needs_no_acknowledgement(tmp_path) -> None:
    fake = FakeLLM(_four_replies() + ["重跑执行", VERIFY_PASS, "# 重跑交付"])
    with _client(tmp_path, fake) as client:
        run_id = _complete_run(client)
        checkpoint_id = _plan_checkpoint(client, run_id)
        resp = client.post(
            f"/api/v1/runs/{run_id}/rewind",
            json={"checkpoint_id": checkpoint_id},
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        rewound = _rewind_event(client, run_id)
        assert rewound["payload"]["side_effects"] == {"count": 0, "tools": []}


def test_fork_with_checkpoint_requires_acknowledgement(tmp_path) -> None:
    settings = _settings(tmp_path, "llm.db", llm_enabled=True)
    with _client(tmp_path, FakeLLM(_four_replies())) as client:
        run_id = _complete_run(client)
        checkpoint_id = _plan_checkpoint(client, run_id)
        _append_tool_events(
            settings, run_id, [_tool_payload("notion_update", risk="danger")]
        )

        blocked = client.post(
            f"/api/v1/runs/{run_id}/fork",
            json={"checkpoint_id": checkpoint_id},
            headers=ACCOUNT,
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "run.side_effects_present"
        assert blocked.json()["details"]["action"] == "fork"

        # 不带 checkpoint 的 fork 不重放父 run 节点,无需确认
        plain = client.post(f"/api/v1/runs/{run_id}/fork", json={}, headers=ACCOUNT)
        assert plain.status_code == 201

        allowed = client.post(
            f"/api/v1/runs/{run_id}/fork",
            json={"checkpoint_id": checkpoint_id, "acknowledge_side_effects": True},
            headers=ACCOUNT,
        )
        assert allowed.status_code == 201
        child_id = allowed.json()["id"]
        created = next(
            e for e in _events(client, child_id) if e["type"] == "run.created.v1"
        )
        assert created["payload"]["side_effects"] == {
            "count": 1,
            "tools": ["notion_update"],
        }
