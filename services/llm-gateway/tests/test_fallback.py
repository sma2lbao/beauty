"""模型 fallback(model-routing §5.3):临时故障切候选、参数错误不切、日志/指标留痕。

litellm 全打桩:按请求模型分发桩行为,零网络。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import litellm
import pytest
from common.metrics import service_label
from common.service_testkit import app_client, build_settings
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app
from llm_gateway.metrics import llm_fallbacks_total
from tests.fakes import chunk, response, sse_frames, stream

PRIMARY = "prov-a/a-model"
BACKUP = "prov-b/b-model"

BODY = {
    "model": PRIMARY,
    "messages": [{"role": "user", "content": "你好"}],
}


@contextmanager
def _client(**overrides: Any) -> Iterator[TestClient]:
    settings = build_settings(Settings, available_models=[], **overrides)
    with app_client(create_app(settings)) as client:
        yield client


def _rate_limit(model: str) -> Exception:
    return litellm.RateLimitError(
        message=f"{model} 限流", llm_provider=model.split("/")[0], model=model
    )


def _unavailable(model: str) -> Exception:
    return litellm.ServiceUnavailableError(
        message=f"{model} 503", llm_provider=model.split("/")[0], model=model
    )


def _bad_request(model: str) -> Exception:
    return litellm.BadRequestError(
        message=f"{model} 参数错误", model=model, llm_provider=model.split("/")[0]
    )


def _dispatch(behaviour: dict[str, Any], calls: list[str]):
    """按 model 分发的 acompletion 桩:值是可调用(返回响应/抛错)或响应。"""

    async def fake_acompletion(**kwargs: Any) -> Any:
        model = kwargs["model"]
        calls.append(model)
        action = behaviour[model]
        result = action() if callable(action) else action
        if isinstance(result, BaseException):
            raise result
        return result

    return fake_acompletion


def test_completion_switches_to_fallback(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _dispatch(
            {
                PRIMARY: lambda: _rate_limit(PRIMARY),
                BACKUP: lambda: response(model=BACKUP, content="备用模型答复"),
            },
            calls,
        ),
    )
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "备用模型答复"
    assert data["model"] == BACKUP
    assert calls == [PRIMARY, BACKUP]
    hops = data["fallbacks"]
    assert len(hops) == 1
    assert hops[0]["from_model"] == PRIMARY
    assert hops[0]["to_model"] == BACKUP
    assert hops[0]["code"] == "llm.upstream_error"
    assert hops[0]["message"].endswith(f"{PRIMARY} 限流")


def test_completion_without_fallbacks_keeps_null(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(litellm, "acompletion", _dispatch({PRIMARY: response()}, calls))
    with _client() as client:
        resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 200
    assert resp.json()["fallbacks"] is None


def test_completion_all_candidates_fail(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _dispatch(
            {
                PRIMARY: lambda: _rate_limit(PRIMARY),
                BACKUP: lambda: _unavailable(BACKUP),
            },
            calls,
        ),
    )
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == "llm.upstream_error"
    details = body["details"]
    assert [attempt["model"] for attempt in details["attempts"]] == [PRIMARY, BACKUP]
    assert [hop["to_model"] for hop in details["fallbacks"]] == [BACKUP]
    assert "503" in details["attempts"][1]["error"]
    assert calls == [PRIMARY, BACKUP]


def test_completion_non_switchable_error_stops_immediately(monkeypatch) -> None:
    """4xx 参数/鉴权错误是调用方问题,换模型只会掩盖:直接 502 不切换。"""
    calls: list[str] = []
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _dispatch({PRIMARY: lambda: _bad_request(PRIMARY)}, calls),
    )
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 502
    details = resp.json()["details"]
    assert [attempt["model"] for attempt in details["attempts"]] == [PRIMARY]
    assert details["fallbacks"] == []
    assert calls == [PRIMARY]  # 备用模型一次都没被调用


def test_completion_dedupes_candidates(monkeypatch) -> None:
    """备用链里重复主模型/自身重复项都不产生第二次尝试。"""
    calls: list[str] = []
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _dispatch({PRIMARY: lambda: _rate_limit(PRIMARY)}, calls),
    )
    with _client(fallback_models=[PRIMARY, BACKUP, BACKUP]) as client:
        resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 502
    # PRIMARY 被去重,只剩 BACKUP 一次尝试
    assert calls == [PRIMARY, BACKUP]


def test_completion_switches_on_duck_typed_status(monkeypatch) -> None:
    """非 litellm 异常但带 5xx 状态码(如 openai.APIStatusError)也可切换。"""

    class HttpError(Exception):
        status_code = 503

    calls: list[str] = []
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _dispatch(
            {
                PRIMARY: lambda: HttpError("上游 503"),
                BACKUP: lambda: response(model=BACKUP),
            },
            calls,
        ),
    )
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 200
    assert calls == [PRIMARY, BACKUP]


def test_completion_does_not_switch_on_duck_typed_4xx(monkeypatch) -> None:
    class HttpError(Exception):
        status_code = 401

    calls: list[str] = []
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _dispatch({PRIMARY: lambda: HttpError("鉴权失败")}, calls),
    )
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 502
    assert calls == [PRIMARY]


def test_fallback_metric_and_warning_log(monkeypatch) -> None:
    """切换留痕:llm_fallbacks_total 按 provider 对计数,日志 warn 一条。"""
    calls: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs["model"])
        if kwargs["model"] == "provx-a/primary":
            raise _rate_limit("provx-a/primary")
        return response(model="provx-b/secondary")

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client(fallback_models=["provx-b/secondary"]) as client:
        with capture_logs() as logs:
            resp = client.post(
                "/v1/completions",
                json={**BODY, "model": "provx-a/primary"},
            )

    assert resp.status_code == 200
    events = [item for item in logs if item["event"] == "llm_fallback"]
    assert len(events) == 1
    assert events[0]["from_model"] == "provx-a/primary"
    assert events[0]["to_model"] == "provx-b/secondary"

    counter = llm_fallbacks_total.labels(
        service=service_label(),
        from_provider="provx-a",
        to_provider="provx-b",
        code="llm.upstream_error",
    )
    assert counter._value.get() == 1


def test_stream_switches_before_first_delta(monkeypatch) -> None:
    """开流前的临时故障可以换候选;成功收尾的 done 片带 fallbacks。"""

    def behaviour(model: str) -> Any:
        if model == PRIMARY:
            return _rate_limit(PRIMARY)
        return stream([chunk("备", model=BACKUP), chunk("用", model=BACKUP)])

    async def fake_acompletion(**kwargs: Any) -> Any:
        result = behaviour(kwargs["model"])
        if isinstance(result, BaseException):
            raise result
        return await result(**kwargs)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post("/v1/completions/stream", json=BODY)

    assert resp.status_code == 200
    frames = sse_frames(resp.text)
    assert [frame.get("delta", "") for frame in frames] == ["备", "用", ""]
    done = frames[-1]
    assert done["done"] is True
    assert done["content"] == "备用"
    assert done["model"] == BACKUP
    assert done["fallbacks"][0]["to_model"] == BACKUP
    assert all("fallbacks" not in frame for frame in frames[:-1])


def test_stream_all_candidates_fail_at_open(monkeypatch) -> None:
    async def fake_acompletion(**kwargs: Any) -> Any:
        raise (
            _rate_limit(PRIMARY) if kwargs["model"] == PRIMARY else _unavailable(BACKUP)
        )

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post("/v1/completions/stream", json=BODY)

    frames = sse_frames(resp.text)
    assert len(frames) == 1
    assert frames[0]["error"]["code"] == "llm.upstream_error"
    assert "503" in frames[0]["error"]["message"]


def test_stream_non_switchable_open_error_is_error_frame(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        litellm,
        "acompletion",
        _dispatch({PRIMARY: lambda: _bad_request(PRIMARY)}, calls),
    )
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post("/v1/completions/stream", json=BODY)

    frames = sse_frames(resp.text)
    assert len(frames) == 1
    assert frames[0]["error"]["code"] == "llm.upstream_error"
    assert calls == [PRIMARY]


def test_stream_midway_failure_does_not_fall_back(monkeypatch) -> None:
    """正文已经下发后失败:只发 error 片,绝不重试(否则调用方收到重复正文)。"""
    calls: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs["model"])

        async def chunks() -> Any:
            yield chunk("前半段", model=PRIMARY)
            raise litellm.APIConnectionError(
                message="连接断了", llm_provider="prov-a", model=PRIMARY
            )

        return chunks()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post("/v1/completions/stream", json=BODY)

    frames = sse_frames(resp.text)
    assert [frame.get("delta", "") for frame in frames] == ["前半段", ""]
    assert frames[-1]["error"]["code"] == "llm.upstream_error"
    assert calls == [PRIMARY]  # 没有切换


@pytest.mark.parametrize("streaming", [False, True])
def test_fallback_message_is_clipped(monkeypatch, streaming: bool) -> None:
    """FallbackHop.message 截断到 500 字,长报错不灌爆响应。"""
    long_error = "x" * 900

    async def fake_acompletion(**kwargs: Any) -> Any:
        if kwargs["model"] == PRIMARY:
            raise litellm.RateLimitError(
                message=long_error, llm_provider="prov-a", model=PRIMARY
            )
        if kwargs.get("stream"):
            return await stream([chunk("ok", model=BACKUP)])(**kwargs)
        return response(model=BACKUP)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    route = "/v1/completions/stream" if streaming else "/v1/completions"
    with _client(fallback_models=[BACKUP]) as client:
        resp = client.post(route, json=BODY)

    if streaming:
        done = sse_frames(resp.text)[-1]
    else:
        done = resp.json()
    message = done["fallbacks"][0]["message"]
    assert len(message) == 500  # 499 字 + 省略号,严格不超 500
    assert message.endswith("…")
