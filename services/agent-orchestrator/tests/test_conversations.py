"""conversation 域:发消息 → run 联动、assistant 消息持久化、多轮上下文注入。

stub 路径(client fixture,LLM=None)覆盖编排语义(自动规划/追加指令);
LLM 路径(_client + FakeLLM)覆盖失败投影与提示词注入。
"""

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, FakeLLM, _client


def _create_conversation(client: TestClient) -> str:
    resp = client.post("/api/v1/conversations", json={}, headers=TENANT)
    assert resp.status_code == 201
    return resp.json()["id"]


def _post_message(client: TestClient, conversation_id: str, content: str) -> dict:
    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=TENANT,
    )
    assert resp.status_code == 201
    return resp.json()


def _messages(client: TestClient, conversation_id: str) -> list[dict]:
    resp = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=TENANT
    )
    assert resp.status_code == 200
    return resp.json()


def _detail(client: TestClient, conversation_id: str) -> dict:
    resp = client.get(f"/api/v1/conversations/{conversation_id}", headers=TENANT)
    assert resp.status_code == 200
    return resp.json()


def test_first_message_creates_run_and_autoplans(client: TestClient) -> None:
    conversation_id = _create_conversation(client)

    result = _post_message(client, conversation_id, "把本周邮件整理成周报")

    assert result["mode"] == "run_created"
    run = result["run"]
    # 发消息即规划:不用再去控制台点「生成计划」
    assert run["status"] == "awaiting_approval"
    assert run["conversation_id"] == conversation_id
    assert result["message"]["role"] == "user"
    assert result["message"]["run_id"] == run["id"]

    detail = _detail(client, conversation_id)
    assert detail["active_run_id"] == run["id"]
    assert detail["title"] == "把本周邮件整理成周报"  # 首条消息自动定题

    messages = _messages(client, conversation_id)
    assert [m["role"] for m in messages] == ["user"]
    assert messages[0]["content"] == "把本周邮件整理成周报"


def test_create_conversation_defaults_placeholder_title(
    client: TestClient,
) -> None:
    conversation_id = _create_conversation(client)
    assert _detail(client, conversation_id)["title"] == "新对话"


def test_first_message_title_truncates(client: TestClient) -> None:
    conversation_id = _create_conversation(client)
    _post_message(client, conversation_id, "啊" * 50)
    assert _detail(client, conversation_id)["title"] == "啊" * 40 + "…"


def test_message_while_run_in_flight_becomes_instruction(
    client: TestClient,
) -> None:
    conversation_id = _create_conversation(client)
    first = _post_message(client, conversation_id, "把本周邮件整理成周报")

    second = _post_message(client, conversation_id, "顺便看看日历")

    assert second["mode"] == "instruction_queued"
    assert second["run"]["id"] == first["run"]["id"]  # 不新开一跑
    messages = _messages(client, conversation_id)
    assert [m["role"] for m in messages] == ["user", "user"]
    assert messages[1]["run_id"] == first["run"]["id"]

    instructions = client.get(
        f"/api/v1/runs/{first['run']['id']}/instructions", headers=TENANT
    ).json()
    assert [item["content"] for item in instructions] == ["顺便看看日历"]
    assert all(item["status"] == "queued" for item in instructions)


def test_approve_persists_assistant_message_with_artifact(
    client: TestClient,
) -> None:
    conversation_id = _create_conversation(client)
    run_id = _post_message(client, conversation_id, "把本周邮件整理成周报")["run"]["id"]

    done = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
    assert done["status"] == "completed"

    messages = _messages(client, conversation_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assistant = messages[-1]
    assert assistant["run_id"] == run_id
    assert "## 执行结果" in assistant["content"]  # 交付物正文即 agent 的回复

    # 完成后 active_run 归空(对话页不再挂 SSE)
    assert _detail(client, conversation_id)["active_run_id"] is None


def test_reject_persists_assistant_message_then_new_run(
    client: TestClient,
) -> None:
    conversation_id = _create_conversation(client)
    run_id = _post_message(client, conversation_id, "把本周邮件整理成周报")["run"]["id"]

    blocked = client.post(
        f"/api/v1/runs/{run_id}/reject", json={"reason": "先别发"}, headers=TENANT
    ).json()
    assert blocked["status"] == "blocked"

    messages = _messages(client, conversation_id)
    assert messages[-1]["role"] == "assistant"
    assert "计划被拒绝:先别发" in messages[-1]["content"]

    # blocked 对对话是停点:新消息另开一跑,而不是挂在旧图上
    second = _post_message(client, conversation_id, "改成只看今天的")
    assert second["mode"] == "run_created"
    assert second["run"]["id"] != run_id
    assert second["run"]["conversation_id"] == conversation_id


def test_plan_failure_persists_assistant_message(tmp_path) -> None:
    fake = FakeLLM([], fail_on_call=1)
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)

        result = _post_message(client, conversation_id, "汇总本周邮件")

        assert result["run"]["status"] == "failed"
        messages = _messages(client, conversation_id)
        assert messages[-1]["role"] == "assistant"
        assert "执行失败" in messages[-1]["content"]
        assert "模型挂了" in messages[-1]["content"]


def test_conversation_history_reaches_plan_prompt(tmp_path) -> None:
    fake = FakeLLM(
        [
            "# 执行计划(第一轮)\n- [ ] 汇总",
            "执行结果:第一轮完成",
            "# 第一轮周报\n\n- 邮件已汇总",
            "# 执行计划(第二轮)\n- [ ] 加上日历",
        ]
    )
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        first = _post_message(client, conversation_id, "把本周邮件整理成周报")
        client.post(f"/api/v1/runs/{first['run']['id']}/approve", headers=TENANT)

        second = _post_message(client, conversation_id, "接着把日历也汇总进来")

        assert second["mode"] == "run_created"
        # 第 4 次调用是第二轮的 plan:必须带上第一轮的对话上下文
        plan_prompt = fake.calls[3][1]["content"]
        assert "之前的对话" in plan_prompt
        assert "把本周邮件整理成周报" in plan_prompt
        assert "第一轮周报" in plan_prompt  # 交付物也在上下文里
        # 触发消息本身是 goal/标题(标题由它派生),不会重复出现在历史块里
        history_block = plan_prompt.split("之前的对话", 1)[1]
        assert "接着把日历也汇总进来" not in history_block


def test_tenant_isolation(client: TestClient) -> None:
    conversation_id = _create_conversation(client)

    assert (
        client.get(
            f"/api/v1/conversations/{conversation_id}", headers=OTHER
        ).status_code
        == 404
    )
    resp = client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=OTHER
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "conversation.not_found"
    assert (
        client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "偷看"},
            headers=OTHER,
        ).status_code
        == 404
    )
    # 列表互不可见
    assert client.get("/api/v1/conversations", headers=OTHER).json() == []


def test_list_conversations_orders_by_recent_activity(client: TestClient) -> None:
    a = _create_conversation(client)
    b = _create_conversation(client)
    listing = client.get("/api/v1/conversations", headers=TENANT).json()
    assert [c["id"] for c in listing] == [b, a]  # 创建新的在前

    _post_message(client, a, "旧对话来了一条新消息")
    listing = client.get("/api/v1/conversations", headers=TENANT).json()
    assert [c["id"] for c in listing] == [a, b]  # 消息把 a 顶到最前
