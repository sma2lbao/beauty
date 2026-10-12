"""写操作确认闸(§6.2):写工具先中断等人工放行,拒绝则不执行、如实记录。"""

from agent_orchestrator.services.llm import LLMReply, ToolCall
from tests.conftest import TENANT, FakeConnector, FakeLLM, _create_run, _tool_client

SEND_ARGS = (
    '{"connection_id": "conn-1", "to": "boss@example.com", '
    '"subject": "周报", "body": "本周汇总"}'
)


def _send_call() -> LLMReply:
    """一次请求两个工具:只读 list_connections(先执行)+ 写 send_email(待确认)。"""
    return LLMReply(
        content="",
        tool_calls=(
            ToolCall(id="call_list", name="list_connections", arguments="{}"),
            ToolCall(id="call_w1", name="send_email", arguments=SEND_ARGS),
        ),
    )


def test_write_tool_waits_for_confirmation_then_executes(tmp_path) -> None:
    verify_pass = '{"summary": "已发送", "verdicts": {"邮件已汇总": true}}'
    fake = FakeLLM(
        [
            "# 计划",
            _send_call(),  # execute:请求写操作 → 中断
            "已发送周报给 boss@example.com",  # 放行后 execute 收尾
            verify_pass,
            "# 交付物",
        ]
    )
    connector = FakeConnector()
    with _tool_client(tmp_path, fake, connector) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)

        # 1) 停在等待确认;写操作绝不能已经执行
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "awaiting_confirmation"
        assert connector.sent == []

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        required = next(
            e for e in events if e["type"] == "run.confirmation.required.v1"
        )
        assert required["payload"]["pending_writes"][0]["name"] == "send_email"

        # 2) 放行 → 执行写操作 → 继续到 completed
        run = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve"},
            headers=TENANT,
        ).json()
        assert run["status"] == "completed"
        assert connector.sent == [("boss@example.com", "周报", "本周汇总")]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        types = [e["type"] for e in events]
        assert "run.confirmation.received.v1" in types
        # 工具事件不重复:只读调用一次 + 写操作一次(§6.3 add reducer 增量落库)
        names = [
            e["payload"]["name"] for e in events if e["type"] == "run.tool.called.v1"
        ]
        assert names == ["list_connections", "send_email"]
        # 顺序语义:只读在闸门之前,写操作在人工确认之后
        list_index = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "run.tool.called.v1"
            and e["payload"]["name"] == "list_connections"
        )
        send_index = next(
            i
            for i, e in enumerate(events)
            if e["type"] == "run.tool.called.v1"
            and e["payload"]["name"] == "send_email"
        )
        assert list_index < types.index("run.confirmation.required.v1")
        assert send_index > types.index("run.confirmation.received.v1")
        assert send_index < types.index("run.completed.v1")
        assert names == ["list_connections", "send_email"]


def test_write_tool_rejected_skips_execution(tmp_path) -> None:
    """拒绝写操作 → 不执行;拒绝原因进 results 并由交付物如实呈现。"""
    verify_pass = '{"summary": "未发送", "verdicts": {"邮件已汇总": true}}'
    fake = FakeLLM(["# 计划", _send_call(), verify_pass, "# 交付物"])
    connector = FakeConnector()
    with _tool_client(tmp_path, fake, connector) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "awaiting_confirmation"

        run = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "reject", "reason": "先别发"},
            headers=TENANT,
        ).json()
        assert run["status"] == "completed"
        assert connector.sent == []  # 关键:拒绝即不执行

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        results = next(e for e in events if e["type"] == "run.completed.v1")["payload"][
            "results"
        ]
        assert any("写操作被拒绝:先别发" in item for item in results)
        # 只读工具执行了;写操作一次都没执行
        names = [
            e["payload"]["name"] for e in events if e["type"] == "run.tool.called.v1"
        ]
        assert names == ["list_connections"]
        send_events = [
            e
            for e in events
            if e["type"] == "run.tool.called.v1"
            and e["payload"]["name"] == "send_email"
        ]
        assert send_events == []


def test_confirm_guard_requires_awaiting_state(tmp_path) -> None:
    """非 awaiting_confirmation 状态确认 → 409(状态守卫)。"""
    fake = FakeLLM(
        [
            "# 计划",
            "执行",
            '{"summary": "ok", "verdicts": {"邮件已汇总": true}}',
            "# 交付物",
        ]
    )
    connector = FakeConnector()
    with _tool_client(tmp_path, fake, connector) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        # 尚未 approve:仍处于 awaiting_approval
        resp = client.post(
            f"/api/v1/runs/{run_id}/confirm",
            json={"action": "approve"},
            headers=TENANT,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "run.invalid_state"


def test_confirmation_cycles_are_capped(tmp_path) -> None:
    """连续重复请求写操作:确认 ≤3 轮,之后如实告知未执行(防确认死循环)。"""
    verify_pass = '{"summary": "ok", "verdicts": {"邮件已汇总": true}}'
    fake = FakeLLM(
        [
            "# 计划",
            _send_call(),
            _send_call(),
            _send_call(),
            _send_call(),  # 第 4 次:超过上限,不执行
            "确认次数已达上限,未发送",
            verify_pass,
            "# 交付物",
        ]
    )
    connector = FakeConnector()
    with _tool_client(tmp_path, fake, connector) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        for _ in range(3):
            assert run["status"] == "awaiting_confirmation"
            run = client.post(
                f"/api/v1/runs/{run_id}/confirm",
                json={"action": "approve"},
                headers=TENANT,
            ).json()
        assert run["status"] == "completed"
        assert len(connector.sent) == 3  # 上限 3 次,第 4 次请求被跳过
