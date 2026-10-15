"""completions:litellm 全部打桩——不发真实请求、不耗 token。"""

import litellm
from fastapi.testclient import TestClient

BODY = {
    "messages": [
        {"role": "system", "content": "你是周报助手"},
        {"role": "user", "content": "汇总本周邮件"},
    ]
}


def _fake_response(content: str = "好的,周报如下"):
    message = type("M", (), {"content": content})()
    choice = type("C", (), {"message": message})()
    usage = type(
        "U",
        (),
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )()
    return type(
        "R",
        (),
        {"choices": [choice], "usage": usage, "model": "deepseek/deepseek-chat"},
    )()


def test_completion_success_default_model(client: TestClient, monkeypatch) -> None:
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    resp = client.post("/v1/completions", json=BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "好的,周报如下"
    assert data["model"] == "deepseek/deepseek-chat"
    assert data["usage"]["total_tokens"] == 15
    assert captured["model"] == "deepseek/deepseek-chat"  # 不传 model 走默认路由


def test_completion_explicit_model(client: TestClient, monkeypatch) -> None:
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    resp = client.post("/v1/completions", json={**BODY, "model": "gpt-4o-mini"})
    assert resp.status_code == 200
    assert captured["model"] == "gpt-4o-mini"


def test_completion_upstream_error_maps_502(client: TestClient, monkeypatch) -> None:
    async def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(litellm, "acompletion", boom)
    resp = client.post("/v1/completions", json=BODY)
    assert resp.status_code == 502
    assert resp.json()["code"] == "llm.upstream_error"


def test_completion_validation(client: TestClient) -> None:
    assert client.post("/v1/completions", json={"messages": []}).status_code == 422
    resp = client.post(
        "/v1/completions", json={"messages": [{"role": "hacker", "content": "x"}]}
    )
    assert resp.status_code == 422


def test_tools_passthrough_and_tool_calls_normalized(
    client: TestClient, monkeypatch
) -> None:
    """tools 原样透传给 litellm;响应的 tool_calls 归一化为嵌套形状。"""
    captured: dict = {}

    def fn(name: str, arguments: str):
        return type("F", (), {"name": name, "arguments": arguments})()

    def call(call_id: str, name: str, arguments: str):
        return type("TC", (), {"id": call_id, "function": fn(name, arguments)})()

    message = type(
        "M",
        (),
        {"content": None, "tool_calls": [call("call_1", "list_connections", "{}")]},
    )()
    fake_message = type(
        "R", (), {"choices": [type("C", (), {"message": message})()], "usage": None}
    )()

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return fake_message

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "list_connections",
                "description": "列连接",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    resp = client.post("/v1/completions", json={**BODY, "tools": tools})
    assert resp.status_code == 200
    data = resp.json()
    assert captured["tools"] == tools
    assert data["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "list_connections", "arguments": "{}"},
        }
    ]
    assert data["content"] == ""


def test_tool_role_message_shape_passthrough(client: TestClient, monkeypatch) -> None:
    """assistant(tool_calls)+ tool(tool_call_id)历史原样进 litellm。"""
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        message = type("M", (), {"content": "ok", "tool_calls": None})()
        return type(
            "R", (), {"choices": [type("C", (), {"message": message})()], "usage": None}
        )()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    resp = client.post(
        "/v1/completions",
        json={
            "messages": [
                {"role": "user", "content": "看邮件"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "fetch_emails", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": '{"emails":[]}'},
            ]
        },
    )
    assert resp.status_code == 200
    sent = captured["messages"]
    assert sent[1]["tool_calls"][0]["function"]["name"] == "fetch_emails"
    assert "content" not in sent[1]  # None 字段不落进请求
    assert sent[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"emails":[]}',
    }


def _fake_stream(pieces: list[str], captured: dict | None = None):
    """litellm 流式:异步生成器,每片一个 choices[0].delta.content。"""

    async def fake_acompletion(**kwargs):
        if captured is not None:
            captured.update(kwargs)

        async def chunks():
            for piece in pieces:
                delta = type("D", (), {"content": piece})()
                choice = type("C", (), {"delta": delta})()
                yield type(
                    "Chunk",
                    (),
                    {"choices": [choice], "model": "deepseek/deepseek-chat"},
                )()

        return chunks()

    return fake_acompletion


def _sse_frames(text: str) -> list[dict]:
    import json

    frames = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data: "):
            frames.append(json.loads(frame[6:]))
    return frames


def test_completion_stream_deltas_and_done(client: TestClient, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        litellm, "acompletion", _fake_stream(["你", "好", "!"], captured)
    )
    resp = client.post("/v1/completions/stream", json=BODY)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert captured["stream"] is True  # 真的按流式请求上游
    frames = _sse_frames(resp.text)
    assert [frame.get("delta", "") for frame in frames] == ["你", "好", "!", ""]
    assert frames[-1]["done"] is True
    assert frames[-1]["content"] == "你好!"  # 结束片带全文,便于调用方对账
    assert frames[-1]["model"] == "deepseek/deepseek-chat"


def test_completion_stream_upstream_error_is_error_frame(
    client: TestClient, monkeypatch
) -> None:
    async def boom(**kwargs):
        raise RuntimeError("上游 500")

    monkeypatch.setattr(litellm, "acompletion", boom)
    frames = _sse_frames(client.post("/v1/completions/stream", json=BODY).text)

    # 流已按 200 开头,失败以 error 片送达(与 complete 的 502 同 code)
    assert frames[-1]["error"]["code"] == "llm.upstream_error"
    assert "上游 500" in frames[-1]["error"]["message"]


def test_completion_stream_midway_failure_keeps_deltas(
    client: TestClient, monkeypatch
) -> None:
    async def midway(**kwargs):
        async def chunks():
            delta = type("D", (), {"content": "前半段"})()
            choice = type("C", (), {"delta": delta})()
            yield type("Chunk", (), {"choices": [choice], "model": "m"})()
            raise RuntimeError("连接断了")

        return chunks()

    monkeypatch.setattr(litellm, "acompletion", midway)
    frames = _sse_frames(client.post("/v1/completions/stream", json=BODY).text)

    assert frames[0]["delta"] == "前半段"  # 已产出的增量不丢
    assert frames[-1]["error"]["code"] == "llm.upstream_error"
    assert "连接断了" in frames[-1]["error"]["message"]
