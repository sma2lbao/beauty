"""conversation 意图分流(chat/task)、多轮上下文与 SSE 流式端点。"""

from common.errors import UpstreamError
from fastapi.testclient import TestClient

from tests.conftest import TENANT, FakeLLM, _client
from tests.support.conversations import (
    _create_conversation,
    _detail,
    _messages,
    _post_message,
    _stream_message,
)


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
        run_id = _post_message(client, conversation_id, "把本周日程整理成周报")["run"][
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


def test_stream_chat_streams_deltas_and_persists_reply(tmp_path) -> None:
    fake = FakeLLM(['{"mode": "chat"}', "你好!"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)

        resp, events = _stream_message(client, conversation_id, "你好")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        names = [name for name, _ in events]
        assert names[0] == "meta" and names[-1] == "done"
        assert "delta" in names  # 真的边输出边发

        meta = events[0][1]
        assert meta["mode"] == "chat" and meta["run"] is None
        assert meta["message"]["content"] == "你好"
        # 增量拼起来就是完整回复
        assert "".join(p["text"] for n, p in events if n == "delta") == "你好!"
        assert events[-1][1]["reply"]["content"] == "你好!"
        # 落库:回放能看到这一问一答,且没有产生任何 run
        assert [m["role"] for m in _messages(client, conversation_id)] == [
            "user",
            "assistant",
        ]
        assert client.get("/api/v1/runs?limit=50", headers=TENANT).json() == []


def test_stream_task_sends_meta_without_deltas(tmp_path) -> None:
    fake = FakeLLM(['{"mode": "task"}', "# 执行计划\n- [ ] 汇总"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)

        _, events = _stream_message(client, conversation_id, "把本周日程整理成周报")

        # 任务路径只发 meta + done,进展由 run 自己的事件流呈现
        assert [name for name, _ in events] == ["meta", "done"]
        meta = events[0][1]
        assert meta["mode"] == "run_created"
        assert meta["run"]["status"] == "awaiting_approval"
        assert events[-1][1]["reply"] is None
        # 用户消息已落库,assistant 回复要等 run 终态才投影
        assert [m["role"] for m in _messages(client, conversation_id)] == ["user"]


def test_stream_chat_midway_failure_keeps_partial_reply(tmp_path) -> None:
    class _HalfThenBoom:
        """先吐一段再失败:验证已产出的增量照样落库。"""

        async def stream(self, messages, *, model=None, tools=None):
            yield "前半段"
            raise UpstreamError("llm.upstream_error", "上游断了")

    with _client(tmp_path, _HalfThenBoom()) as client:
        conversation_id = _create_conversation(client)

        # mode=chat 跳过分流,所以这个假客户端只需要 stream
        _, events = _stream_message(client, conversation_id, "你好", mode="chat")

        assert [name for name, _ in events] == ["meta", "delta", "error"]
        assert events[-1][1]["code"] == "llm.upstream_error"
        assert "上游断了" in events[-1][1]["message"]
        # 半截回复不丢:落库后回放得到"前半段"
        messages = _messages(client, conversation_id)
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[-1]["content"] == "前半段"


def test_stream_forced_chat_without_llm_is_409_before_stream(
    client: TestClient,
) -> None:
    conversation_id = _create_conversation(client)

    resp, _ = _stream_message(client, conversation_id, "你好", mode="chat")

    # 开流之前就拒绝:否则只能发一帧错误,HTTP 状态码已经是 200
    assert resp.status_code == 409
    assert resp.json()["code"] == "chat.llm_disabled"


def test_stream_unknown_conversation_is_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/conversations/nope/messages/stream",
        json={"content": "你好"},
        headers=TENANT,
    )

    assert resp.status_code == 404
