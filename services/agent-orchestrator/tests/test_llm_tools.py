"""execute 阶段 function-calling:工具结果回喂、轮次上限与错误回喂。"""

from agent_orchestrator.services.llm import LLMReply, ToolCall
from tests.conftest import (
    TENANT,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _tool_client,
)


def test_execute_tool_loop_feeds_result_back(tmp_path) -> None:
    """execute 走 function-calling:工具结果回喂模型,调用落 run.tool.called.v1。"""
    tool_reply = LLMReply(
        content="",
        tool_calls=(
            ToolCall(
                id="call_1",
                name="search_knowledge",
                arguments='{"query": "本周日程"}',
            ),
        ),
    )
    final = "已检索到 1 条资料:客户 A 合同"
    verify_pass = '{"summary": "验收通过", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(
        ["# 执行计划(LLM)", tool_reply, final, verify_pass, "# 资料清单交付物"]
    )
    knowledge = FakeKnowledge()
    with _tool_client(tmp_path, fake, knowledge) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"

        # 第 2 个 execute 轮次的消息里带着工具结果(role=tool)
        tool_messages = [m for m in fake.calls[2] if m["role"] == "tool"]
        assert tool_messages and "客户 A 合同" in tool_messages[0]["content"]
        # 工具只在 execute 下发:plan 那次为 None
        assert fake.tools_seen[0] is None
        assert fake.tools_seen[1][0]["function"]["name"] == "search_knowledge"
        assert knowledge.queries == [("t-1", "本周日程", 5)]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        types = [e["type"] for e in events]
        assert "run.tool.called.v1" in types
        tool_event = next(e for e in events if e["type"] == "run.tool.called.v1")
        assert tool_event["payload"]["name"] == "search_knowledge"
        assert tool_event["payload"]["ok"] is True
        # 事件顺序:工具调用记在终态之前
        assert types.index("run.tool.called.v1") < types.index("run.completed.v1")


def test_execute_tool_rounds_cap_forces_final_answer(tmp_path) -> None:
    """execute 轮次用尽仍在要工具:补一次不带工具的收束,别把成功读到的结果丢掉。"""
    tool_reply = LLMReply(
        content="",
        tool_calls=(
            ToolCall(
                id="call_1",
                name="search_knowledge",
                arguments='{"query": "本周日程"}',
            ),
        ),
    )
    # 每一轮都只要工具(不收敛):execute 会用满 MAX_TOOL_ROUNDS 轮
    fake = FakeLLM(
        [
            "# 执行计划(LLM)",
            tool_reply,
            tool_reply,
            tool_reply,
            tool_reply,
            tool_reply,
            "已读取:4 个画板",
            '{"summary": "验收通过", "verdicts": {"日程已整理": true}}',
            "# 交付物",
        ]
    )
    knowledge = FakeKnowledge()
    with _tool_client(tmp_path, fake, knowledge) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"

        # 第 6 次调用(index 6)是收束调用:没有下发工具
        assert fake.tools_seen[6] is None
        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        tool_events = [e for e in events if e["type"] == "run.tool.called.v1"]
        assert len(tool_events) == 5
        completed = next(e for e in events if e["type"] == "run.completed.v1")
        assert completed["payload"]["results"][0] == "已读取:4 个画板"
        assert "未给出结论" not in str(completed["payload"]["results"])


def test_tool_error_fed_back_to_model(tmp_path) -> None:
    """工具执行失败不炸图:错误文本回喂模型,事件记为 ok=false。"""
    tool_reply = LLMReply(
        content="",
        tool_calls=(
            ToolCall(
                id="call_1",
                name="search_knowledge",
                arguments='{"query": "本周日程"}',
            ),
        ),
    )
    final = "检索失败:知识库异常,已如实报告"
    verify_pass = '{"summary": "验收通过", "verdicts": {"日程已整理": true}}'
    fake = FakeLLM(["# 计划", tool_reply, final, verify_pass, "# 失败说明交付物"])
    knowledge = FakeKnowledge(fail=True)
    with _tool_client(tmp_path, fake, knowledge) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"  # 工具坏 ≠ run 坏(§6.1)

        tool_messages = [m for m in fake.calls[2] if m["role"] == "tool"]
        assert "error" in tool_messages[-1]["content"]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        tool_event = next(e for e in events if e["type"] == "run.tool.called.v1")
        assert tool_event["payload"]["ok"] is False
        assert "知识库挂了" in tool_event["payload"]["detail"]
