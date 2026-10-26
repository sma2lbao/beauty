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
                {"role": "user", "content": "看文档"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "list_documents", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": '{"documents":[]}',
                },
            ]
        },
    )
    assert resp.status_code == 200
    sent = captured["messages"]
    assert sent[1]["tool_calls"][0]["function"]["name"] == "list_documents"
    assert "content" not in sent[1]  # None 字段不落进请求
    assert sent[2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"documents":[]}',
    }


def _chunk(piece, as_dict: bool = False):
    """造一片 litellm 流片:str 是正文增量,dict 是 delta 的其余字段。"""
    model = "deepseek/deepseek-chat"
    delta = {"content": piece} if isinstance(piece, str) else dict(piece)
    delta.setdefault("content", None)
    if as_dict:
        return {"choices": [{"delta": delta}], "model": model}
    delta_obj = type("D", (), delta)()
    return type(
        "Chunk",
        (),
        {"choices": [type("C", (), {"delta": delta_obj})()], "model": model},
    )()


def _fake_stream(pieces: list, captured: dict | None = None, as_dict: bool = False):
    """litellm 流式:异步生成器,每片一个 choices[0].delta。

    pieces 里的 str 是正文增量、dict 是 delta 字段(如 tool_calls 分片);
    as_dict=True 时整片用字典形态,覆盖 litellm 的 dict 响应分支。
    """

    async def fake_acompletion(**kwargs):
        if captured is not None:
            captured.update(kwargs)

        async def chunks():
            for piece in pieces:
                yield _chunk(piece, as_dict)

        return chunks()

    return fake_acompletion


def _function(name=None, arguments=None, as_dict: bool = False):
    if as_dict:
        return {"name": name, "arguments": arguments}
    return type("F", (), {"name": name, "arguments": arguments})()


def _fragment(index=None, call_id=None, name=None, arguments=None, as_dict=False):
    """litellm 流式 tool_calls 分片;index=None 表示该片不带 index(单调用)。"""
    fragment = {"id": call_id, "function": _function(name, arguments, as_dict)}
    if index is not None:
        fragment["index"] = index
    if as_dict:
        return fragment
    return type("TC", (), fragment)()


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


_CALL_SEARCH = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "search_knowledge", "arguments": '{"query":"本周"}'},
}


def test_completion_stream_assembles_single_tool_call(
    client: TestClient, monkeypatch
) -> None:
    """单调用:首片带 id/name,arguments 跨片拼接;工具调用只在 done 帧。"""
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _fake_stream(
            [
                "查一下",
                {
                    "tool_calls": [
                        _fragment(0, "call_1", "search_knowledge", '{"query":')
                    ]
                },
                {"tool_calls": [_fragment(arguments='"本周"')]},  # index 缺省按 0
                {"tool_calls": [_fragment(arguments="}")]},
            ]
        ),
    )
    frames = _sse_frames(client.post("/v1/completions/stream", json=BODY).text)

    assert [frame.get("delta") for frame in frames[:-1]] == ["查一下"]
    assert all("tool_calls" not in frame for frame in frames[:-1])  # 半截不外泄
    assert frames[-1]["done"] is True
    assert frames[-1]["content"] == "查一下"
    assert frames[-1]["tool_calls"] == [_CALL_SEARCH]


def test_completion_stream_assembles_parallel_tool_calls(
    client: TestClient, monkeypatch
) -> None:
    """多调用 index=0/1 交错下发,按 index 各归各位;无 arguments 补 "{}"。"""
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _fake_stream(
            [
                {
                    "tool_calls": [
                        _fragment(0, "call_1", "search_knowledge", '{"query":')
                    ]
                },
                {"tool_calls": [_fragment(1, "call_2", "list_connections")]},
                {"tool_calls": [_fragment(0, arguments='"本周"}')]},
            ]
        ),
    )
    frames = _sse_frames(client.post("/v1/completions/stream", json=BODY).text)

    assert frames[-1]["tool_calls"] == [
        _CALL_SEARCH,
        {
            "id": "call_2",
            "type": "function",
            "function": {"name": "list_connections", "arguments": "{}"},
        },
    ]


def test_completion_stream_content_and_tool_calls(
    client: TestClient, monkeypatch
) -> None:
    """正文增量与工具调用并存:delta 帧逐字下发,done 帧两者一起带出。"""
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _fake_stream(
            [
                "正在查",
                {
                    "tool_calls": [
                        _fragment(0, "call_1", "search_knowledge", '{"query":')
                    ]
                },
                "…",
                {"tool_calls": [_fragment(arguments='"本周"}')]},
            ]
        ),
    )
    frames = _sse_frames(client.post("/v1/completions/stream", json=BODY).text)

    assert [frame.get("delta") for frame in frames[:-1]] == ["正在查", "…"]
    assert all("tool_calls" not in frame for frame in frames[:-1])
    assert frames[-1]["content"] == "正在查…"
    assert frames[-1]["tool_calls"] == [_CALL_SEARCH]


def test_completion_stream_without_tool_calls_omits_key(
    client: TestClient, monkeypatch
) -> None:
    """无工具调用:done 帧省略 tool_calls(不输出空数组);空 choices 尾片忽略。"""

    async def fake_acompletion(**kwargs):
        async def chunks():
            yield _chunk("纯正文")
            yield type("Chunk", (), {"choices": [], "model": "m"})()

        return chunks()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    frames = _sse_frames(client.post("/v1/completions/stream", json=BODY).text)

    assert frames[-1]["done"] is True
    assert frames[-1]["content"] == "纯正文"
    assert "tool_calls" not in frames[-1]  # 无工具调用不输出空数组


def test_completion_stream_dict_style_tool_calls(
    client: TestClient, monkeypatch
) -> None:
    """litellm dict 响应形态:分片是普通字典时同样按 index 装配。"""
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _fake_stream(
            [
                {
                    "tool_calls": [
                        _fragment(
                            0,
                            "call_1",
                            "search_knowledge",
                            '{"query":',
                            as_dict=True,
                        )
                    ]
                },
                {"tool_calls": [_fragment(arguments='"本周"}', as_dict=True)]},
            ],
            as_dict=True,
        ),
    )
    frames = _sse_frames(client.post("/v1/completions/stream", json=BODY).text)

    assert frames[-1]["tool_calls"] == [_CALL_SEARCH]


def test_completion_stream_midway_failure_drops_partial_tool_calls(
    client: TestClient, monkeypatch
) -> None:
    """工具分片到一半断流:不落 done 帧,error 帧也不带半截 tool_calls。"""

    async def midway(**kwargs):
        async def chunks():
            yield _chunk(
                {"tool_calls": [_fragment(0, "call_1", "search_knowledge", '{"q')]}
            )
            raise RuntimeError("连接断了")

        return chunks()

    monkeypatch.setattr(litellm, "acompletion", midway)
    frames = _sse_frames(client.post("/v1/completions/stream", json=BODY).text)

    assert frames[-1]["error"]["code"] == "llm.upstream_error"
    assert all("tool_calls" not in frame for frame in frames)


def test_provider_credentials_passed_to_litellm(monkeypatch) -> None:
    from llm_gateway.core.config import Settings
    from llm_gateway.main import create_app

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    app = create_app(
        Settings(env="dev", api_key="sk-test", api_base="http://provider.local")
    )
    with TestClient(app) as client:
        resp = client.post("/v1/completions", json=BODY)
    assert resp.status_code == 200
    assert captured["api_key"] == "sk-test"
    assert captured["api_base"] == "http://provider.local"
