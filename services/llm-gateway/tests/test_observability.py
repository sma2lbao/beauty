"""模型调用观测:llm_request/llm_response 字段、正文开关、token 与耗时指标。

litellm 全打桩(与 test_completions 一致):不发真实请求、不耗 token。
"""

from __future__ import annotations

import asyncio
from typing import Any

import litellm
from common import metrics
from structlog.testing import capture_logs

from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import CompletionRequest
from llm_gateway.services import completions

MODEL = "observe/observe-model"
PROMPT = "你是周报助手" + "x" * 50
CONTENT = "好的,周报如下" + "y" * 2100


def _fake_response() -> Any:
    message = type("M", (), {"content": CONTENT})()
    choice = type("C", (), {"message": message, "finish_reason": "stop"})()
    usage = type(
        "U", (), {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}
    )()
    return type("R", (), {"choices": [choice], "usage": usage, "model": MODEL})()


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, env="dev", log_level="DEBUG", **overrides)


def _run(monkeypatch, settings: Settings) -> list[dict[str, Any]]:
    async def fake_acompletion(**kwargs: Any) -> Any:
        return _fake_response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    request = CompletionRequest(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT}],
        temperature=0.2,
    )
    with capture_logs() as logs:
        asyncio.run(completions.complete(settings, request))
    return logs


def _event(logs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(item for item in logs if item["event"] == name)


def test_request_and_response_are_logged(monkeypatch) -> None:
    logs = _run(monkeypatch, _settings())

    request = _event(logs, "llm_request")
    assert request["provider"] == "observe"
    assert request["model"] == MODEL
    assert request["stream"] is False
    assert request["message_count"] == 1
    assert request["roles"] == "user"
    assert request["prompt_chars"] == len(PROMPT)
    assert request["tool_count"] == 0
    assert request["temperature"] == 0.2

    response = _event(logs, "llm_response")
    assert response["model"] == MODEL
    assert response["finish_reason"] == "stop"
    assert response["prompt_tokens"] == 7
    assert response["completion_tokens"] == 5
    assert response["total_tokens"] == 12
    assert response["chars"] == len(CONTENT)
    assert response["duration_ms"] >= 0
    # 默认不落正文
    assert not [item for item in logs if item["event"] == "llm_content"]


def test_content_is_opt_in_and_truncated(monkeypatch) -> None:
    logs = _run(monkeypatch, _settings(log_content=True))

    contents = [item for item in logs if item["event"] == "llm_content"]
    assert {item["direction"] for item in contents} == {"request", "response"}
    request_payload = next(
        item["payload"] for item in contents if item["direction"] == "request"
    )
    assert request_payload == [{"role": "user", "content": PROMPT}]
    response_payload = next(
        item["payload"] for item in contents if item["direction"] == "response"
    )
    assert response_payload.endswith("…(截断)")
    assert len(response_payload) <= 2000 + len("…(截断)")


def test_tokens_and_latency_are_counted(monkeypatch) -> None:
    _run(monkeypatch, _settings())

    text = metrics.metrics_payload().decode()
    # 单测没装配日志上下文,service 落到默认值 unknown
    assert f'llm_tokens_total{{direction="input",model="{MODEL}"' in text
    assert f'llm_tokens_total{{direction="output",model="{MODEL}"' in text
    assert f'llm_request_duration_seconds_count{{model="{MODEL}"' in text
