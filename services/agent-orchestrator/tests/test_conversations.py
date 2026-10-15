"""conversation 域:意图分流(§6.7)、run 联动、assistant 消息持久化、多轮上下文。

stub 路径(client fixture,LLM=None)覆盖编排语义——没有模型时一律任务路径;
LLM 路径(_client + FakeLLM)覆盖分流判别、直接对话、失败投影与提示词注入。
"""

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, FakeLLM, _client


def _create_conversation(client: TestClient) -> str:
    resp = client.post("/api/v1/conversations", json={}, headers=TENANT)
    assert resp.status_code == 201
    return resp.json()["id"]


def _post_message(
    client: TestClient, conversation_id: str, content: str, *, mode: str = "auto"
) -> dict:
    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": content, "mode": mode},
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
    # 第 1 次是分流(正常),第 2 次 plan 失败 → 失败原因进对话
    fake = FakeLLM(['{"mode": "task"}'], fail_on_call=2)
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
            '{"mode": "task"}',  # 1 分流(第一条消息)
            "# 执行计划(第一轮)\n- [ ] 汇总",
            "执行结果:第一轮完成",
            "# 第一轮周报\n\n- 邮件已汇总",
            '{"mode": "task"}',  # 5 分流(第二条消息)
            "# 执行计划(第二轮)\n- [ ] 加上日历",
        ]
    )
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        first = _post_message(client, conversation_id, "把本周邮件整理成周报")
        client.post(f"/api/v1/runs/{first['run']['id']}/approve", headers=TENANT)

        second = _post_message(client, conversation_id, "接着把日历也汇总进来")

        assert second["mode"] == "run_created"
        # 第 6 次调用是第二轮的 plan:必须带上第一轮的对话上下文
        plan_prompt = fake.calls[5][1]["content"]
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


def test_simple_chat_answers_without_run(tmp_path) -> None:
    fake = FakeLLM(['{"mode": "chat"}', "你好!有什么可以帮你的?"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)

        result = _post_message(client, conversation_id, "你好")

        assert result["mode"] == "chat"
        assert result["run"] is None  # 简单对话不建任务
        assert result["reply"]["role"] == "assistant"
        assert result["reply"]["content"] == "你好!有什么可以帮你的?"
        assert result["reply"]["run_id"] is None  # 直接回复不挂 run

        assert [m["role"] for m in _messages(client, conversation_id)] == [
            "user",
            "assistant",
        ]
        # 整个会话没有产生任何 run
        assert client.get("/api/v1/runs?limit=50", headers=TENANT).json() == []
        assert _detail(client, conversation_id)["active_run_id"] is None


def test_forced_chat_skips_triage(tmp_path) -> None:
    fake = FakeLLM(["直接答一句"])  # 只够一次调用:再去分流就会缺响应
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)

        result = _post_message(client, conversation_id, "你好", mode="chat")

        assert result["mode"] == "chat"
        assert len(fake.calls) == 1  # 显式 chat:不做分流
        assert fake.calls[0][-1]["content"] == "你好"


def test_forced_task_skips_triage(tmp_path) -> None:
    fake = FakeLLM(["# 执行计划\n- [ ] 做", "执行结果:做完", "# 交付物\n\n成品"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)

        result = _post_message(client, conversation_id, "做点什么", mode="task")

        assert result["mode"] == "run_created"
        assert result["run"]["status"] == "awaiting_approval"
        # 显式 task:第一次调用就是 plan(含"目标:"),说明没走分流
        assert "目标:做点什么" in fake.calls[0][1]["content"]


def test_chat_during_inflight_run_does_not_queue_instruction(tmp_path) -> None:
    fake = FakeLLM(
        [
            '{"mode": "task"}',  # 1 分流:第一条是任务
            "# 执行计划\n- [ ] 汇总",
            '{"mode": "chat"}',  # 3 分流:第二条是闲聊
            "好的,我记下了。",
        ]
    )
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        run_id = _post_message(client, conversation_id, "把本周邮件整理成周报")["run"][
            "id"
        ]

        second = _post_message(client, conversation_id, "辛苦了")

        assert second["mode"] == "chat"  # 闲聊不并入任务
        assert second["run"] is None
        # 没有变成追加指令,run 也不受影响
        assert (
            client.get(f"/api/v1/runs/{run_id}/instructions", headers=TENANT).json()
            == []
        )
        assert _detail(client, conversation_id)["active_run_id"] == run_id
        assert [m["role"] for m in _messages(client, conversation_id)] == [
            "user",
            "user",
            "assistant",
        ]


def test_chat_reply_carries_conversation_history(tmp_path) -> None:
    fake = FakeLLM(
        [
            '{"mode": "chat"}',
            "第一轮回复",
            '{"mode": "chat"}',
            "第二轮回复",
        ]
    )
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "我叫小明")

        second = _post_message(client, conversation_id, "我叫什么?")

        assert second["reply"]["content"] == "第二轮回复"
        contents = [m["content"] for m in fake.calls[3]]  # 第二轮 chat 的消息序列
        assert "我叫小明" in contents  # 用户上一轮
        assert "第一轮回复" in contents  # 助手上一轮
        assert contents[-1] == "我叫什么?"  # 本轮在最后


def test_triage_failure_falls_back_to_task(tmp_path) -> None:
    # 第 1 次调用(分流)失败——即便它本会判成 chat,也退回任务路径;
    # FakeLLM 按调用序号取响应,所以第 2 个响应(索引 1)是给 plan 的
    fake = FakeLLM(['{"mode": "chat"}', "# 执行计划\n- [ ] 兜底"], fail_on_call=1)
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)

        result = _post_message(client, conversation_id, "帮我做点什么")

        assert result["mode"] == "run_created"
        assert result["run"]["status"] == "awaiting_approval"


def test_forced_chat_without_llm_is_conflict(client: TestClient) -> None:
    conversation_id = _create_conversation(client)

    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "你好", "mode": "chat"},
        headers=TENANT,
    )

    # 没有模型就没有"直接对话"能力:如实报错,不悄悄改成任务
    assert resp.status_code == 409
    assert resp.json()["code"] == "chat.llm_disabled"


def test_invalid_send_mode_rejected(client: TestClient) -> None:
    conversation_id = _create_conversation(client)

    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "你好", "mode": "whatever"},
        headers=TENANT,
    )

    assert resp.status_code == 422
