"""litellm 打桩共用形状:响应/流片构造 + SSE 帧解析(零网络)。

只给测试用(不在 llm_gateway 包内,不进覆盖率分母)。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def response(
    model: str = "deepseek/deepseek-chat",
    content: str = "好的",
    *,
    usage: tuple[int, int, int] | None = (10, 5, 15),
    tool_calls: Any = None,
) -> Any:
    """造一次非流式 litellm 响应(对象式,与真实 SDK 形状一致)。"""
    message = type("M", (), {"content": content, "tool_calls": tool_calls})()
    choice = type("C", (), {"message": message, "finish_reason": "stop"})()
    usage_obj = None
    if usage is not None:
        usage_obj = type(
            "U",
            (),
            {
                "prompt_tokens": usage[0],
                "completion_tokens": usage[1],
                "total_tokens": usage[2],
            },
        )()
    return type("R", (), {"choices": [choice], "usage": usage_obj, "model": model})()


def chunk(
    piece: str = "",
    *,
    model: str = "deepseek/deepseek-chat",
    usage: Any = None,
) -> Any:
    """造一片流式 litellm 响应(只带正文增量)。"""
    delta = type("D", (), {"content": piece, "tool_calls": None})()
    choice = type("C", (), {"delta": delta})()
    return type("Chunk", (), {"choices": [choice], "model": model, "usage": usage})()


def stream(chunks: Iterable[Any], captured: dict | None = None):
    """造一个流式 acompletion 桩:返回异步生成器,顺序吐出给定流片。"""

    async def fake_acompletion(**kwargs: Any) -> Any:
        if captured is not None:
            captured.update(kwargs)

        async def iterate() -> Any:
            for item in chunks:
                yield item

        return iterate()

    return fake_acompletion


def sse_frames(text: str) -> list[dict[str, Any]]:
    """SSE 响应正文 → data 帧(JSON)列表。"""
    frames: list[dict[str, Any]] = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data: "):
            frames.append(json.loads(frame[6:]))
    return frames
