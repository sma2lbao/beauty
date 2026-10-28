"""知识库技能(§9):search_knowledge 工具与经完整图的调用。"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import build_tools
from tests.conftest import (
    TENANT,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _settings,
    verifier,
)

VERIFY_PASS = '{"summary": "ok", "verdicts": {"日程已整理": true}}'


async def test_search_knowledge_tool_formats_hits() -> None:
    knowledge = FakeKnowledge()
    registry = build_tools("t-1", knowledge=knowledge)

    assert registry.names == ["search_knowledge"]
    assert registry.requires_confirmation("search_knowledge") is False  # 只读

    payload = json.loads(
        await registry.execute("search_knowledge", '{"query": "合同", "limit": 3}')
    )
    assert payload["provider"] == "local"
    assert payload["model"] == "hashing-256"
    assert payload["hits"][0]["title"] == "客户 A 合同"
    assert payload["hits"][0]["score"] == 0.93
    assert knowledge.queries == [("t-1", "合同", 3)]


async def test_search_knowledge_argument_guards() -> None:
    knowledge = FakeKnowledge()
    registry = build_tools("t-1", knowledge=knowledge)

    missing = json.loads(await registry.execute("search_knowledge", "{}"))
    # P1-G §6.5:执行前统一校验参数,错误文本来自 common.tools.validate_arguments
    assert "工具参数无效" in missing["error"]
    assert "query" in missing["error"]

    # 超上限的参数在执行前被 JSON Schema 校验拦下(不再进 handler 夹取)
    over_limit = json.loads(
        await registry.execute("search_knowledge", '{"query": "x", "limit": 999}')
    )
    assert "工具参数无效" in over_limit["error"]
    assert "limit" in over_limit["error"]
    assert knowledge.queries == []  # 未调用 handler


async def test_search_knowledge_error_fed_back() -> None:
    registry = build_tools("t-1", knowledge=FakeKnowledge(fail=True))
    payload = json.loads(
        await registry.execute("search_knowledge", '{"query": "合同"}')
    )
    assert "知识库挂了" in payload["error"]


def test_knowledge_tool_flows_through_graph(tmp_path: Path) -> None:
    """完整图:模型先查知识库,再把检索片段写进交付物。"""
    knowledge = FakeKnowledge()
    search_call = LLMReply(
        content="",
        tool_calls=(
            ToolCall(
                id="k1",
                name="search_knowledge",
                arguments='{"query": "客户 A 合同评审"}',
            ),
        ),
    )
    fake = FakeLLM(
        ["# 计划", search_call, "据知识库:评审在周四,需报价单", VERIFY_PASS, "# 交付物"]
    )
    app = create_app(
        _settings(tmp_path, "knowledge.db", llm_enabled=True, tools_enabled=True),
        llm_client=fake,
        toolkit_factory=lambda tenant: build_tools(tenant, knowledge=knowledge),
        token_verifier=verifier(),
    )

    with TestClient(app) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"

        # 只暴露知识库能力;检索片段经 tool 消息回喂模型
        assert {item["function"]["name"] for item in fake.tools_seen[1]} == {
            "search_knowledge",
            "todo_write",
            "spawn_subagent",
        }
        tool_messages = [m for m in fake.calls[2] if m["role"] == "tool"]
        assert tool_messages and "报价单" in tool_messages[0]["content"]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        tool_event = next(e for e in events if e["type"] == "run.tool.called.v1")
        assert tool_event["payload"]["name"] == "search_knowledge"
        assert tool_event["payload"]["ok"] is True
        assert knowledge.queries == [("t-1", "客户 A 合同评审", 5)]
