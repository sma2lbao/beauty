"""P1-B 应用级 hooks 执行器(docs/design/hooks.md §4):协议编解码与降级策略。

零网络:HTTP 一律 httpx.MockTransport,解析器注入 fake(不解析真实域名)。
覆盖:签名/请求体/截断、响应判定(deny 仅 PreToolUse、skip 仅 PreCompact)、
串行短路、note 清洗与追加、非 2xx/超时/超长/私网目标一律按失败降级、
hooks_fail_closed 收紧为 deny、总开关关闭时零开销放行。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest

from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph.ports import (
    HOOK_ARGUMENT_CHARS,
    HOOK_EVENT_POST_TOOL_USE,
    HOOK_EVENT_PRE_COMPACT,
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_EVENT_SESSION_START,
    HookDecision,
    append_hook_note,
    build_hook_payload,
)
from agent_orchestrator.services import hooks as hooks_service
from agent_orchestrator.services.hooks import (
    ARGUMENT_CHARS,
    EVENT_HEADER,
    ID_HEADER,
    NOTE_CHARS,
    REASON_CHARS,
    RESPONSE_MAX_BYTES,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WebhookHookRunner,
    parse_response,
    sign,
)

PUBLIC_ADDRESS = "93.184.216.34"
TARGET = "https://hooks.example.com/dsh"


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, env="dev", **overrides)


@pytest.fixture(autouse=True)
def _clean_config():
    hooks_service.reset_hooks()
    yield
    hooks_service.reset_hooks()


def _hook(
    *,
    hook_id: str = "hook-1",
    event: str = HOOK_EVENT_PRE_TOOL_USE,
    matcher: str = "*",
    target: str = TARGET,
    secret: str = "s3cr3t",
    timeout_seconds: int = 3,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "id": hook_id,
        "account_id": "a" * 32,
        "event": event,
        "matcher": matcher,
        "kind": "webhook",
        "target": target,
        "secret": secret,
        "timeout_seconds": timeout_seconds,
        "enabled": enabled,
    }


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# ---------------------------------------------------------------- 协议编解码


def test_build_hook_payload_truncates_arguments_and_result() -> None:
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE,
        account_id="a" * 32,
        conversation_id="c1",
        run_id="r1",
        tool="write_file",
        arguments="x" * (HOOK_ARGUMENT_CHARS + 100),
        result="y" * (HOOK_ARGUMENT_CHARS + 100),
        tokens_before=10,
        reason="threshold",
    )
    assert payload["tool"] == "write_file"
    assert payload["account_id"] == "a" * 32
    assert payload["conversation_id"] == "c1"
    assert payload["run_id"] == "r1"
    assert payload["tokens_before"] == 10
    assert payload["reason"] == "threshold"
    assert payload["arguments"].endswith("…(truncated)")
    assert payload["result"].endswith("…(truncated)")
    assert len(payload["arguments"]) == ARGUMENT_CHARS + len("…(truncated)")
    # event 由 runner 在发请求时统一补(快照里不带,避免调用方漏改)
    assert "event" not in payload


def test_build_hook_payload_skips_absent_fields() -> None:
    payload = build_hook_payload(HOOK_EVENT_SESSION_START, account_id="a" * 32)
    assert payload == {"account_id": "a" * 32}


def test_append_hook_note_only_when_note_present() -> None:
    assert (
        append_hook_note('{"ok": true}', hooks_service.HookOutcome()) == '{"ok": true}'
    )
    noted = append_hook_note(
        '{"ok": true}',
        hooks_service.HookOutcome(note="请再校验一次"),
    )
    assert noted.startswith('{"ok": true}')
    assert "[hook note] 请再校验一次" in noted


def test_sign_matches_hmac_contract() -> None:
    timestamp = "1700000000"
    body = b'{"event":"PreToolUse"}'
    expected = hmac.new(
        b"s3cr3t", timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    assert sign("s3cr3t", timestamp, body) == f"sha256={expected}"


def test_parse_response_reads_last_json_line_and_cleans_text() -> None:
    raw = (
        b"ignore this line\n"
        + json.dumps(
            {"decision": "allow", "reason": "why\x01", "note": "note\x02"}
        ).encode()
    )
    outcome = parse_response(raw, HOOK_EVENT_PRE_TOOL_USE)
    assert outcome.decision is HookDecision.ALLOW
    assert outcome.reason == "why"
    assert outcome.note == "note"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"", HookDecision.ALLOW),
        (b"not json", HookDecision.ALLOW),
        (b'["allow"]', HookDecision.ALLOW),
        (b'{"decision": "maybe"}', HookDecision.ALLOW),
        (b'{"decision": "deny"}', HookDecision.DENY),
        (b'{"decision": "skip"}', HookDecision.ALLOW),
    ],
)
def test_parse_response_pre_tool_use_decisions(
    raw: bytes, expected: HookDecision
) -> None:
    assert parse_response(raw, HOOK_EVENT_PRE_TOOL_USE).decision is expected


def test_parse_response_deny_only_effective_for_pre_tool_use() -> None:
    raw = b'{"decision": "deny", "reason": "nope"}'
    assert parse_response(raw, HOOK_EVENT_POST_TOOL_USE).decision is HookDecision.ALLOW
    assert parse_response(raw, HOOK_EVENT_PRE_COMPACT).decision is HookDecision.ALLOW


def test_parse_response_skip_only_effective_for_pre_compact() -> None:
    raw = json.dumps({"decision": "skip", "reason": "别压缩"}).encode()
    outcome = parse_response(raw, HOOK_EVENT_PRE_COMPACT)
    assert outcome.decision is HookDecision.SKIP
    assert outcome.reason == "别压缩"
    assert parse_response(raw, HOOK_EVENT_PRE_TOOL_USE).decision is HookDecision.ALLOW


def test_parse_response_truncates_reason_and_note() -> None:
    raw = json.dumps(
        {"decision": "allow", "reason": "r" * 5000, "note": "n" * 5000}
    ).encode()
    outcome = parse_response(raw, HOOK_EVENT_PRE_TOOL_USE)
    assert len(outcome.reason) == REASON_CHARS
    assert len(outcome.note) == NOTE_CHARS


# ---------------------------------------------------------------- 执行器


async def test_runner_posts_signed_body_with_contract_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response({"decision": "allow", "note": "已留痕"})

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    hooks = [_hook()]
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE,
        account_id="a" * 32,
        tool="read_pdf",
        arguments='{"p":1}',
    )
    outcome = await WebhookHookRunner().run(hooks, HOOK_EVENT_PRE_TOOL_USE, payload)

    assert outcome.decision is HookDecision.ALLOW
    assert outcome.note == "已留痕"
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == TARGET
    assert request.headers[EVENT_HEADER] == HOOK_EVENT_PRE_TOOL_USE
    assert request.headers[ID_HEADER] == "hook-1"
    timestamp = request.headers[TIMESTAMP_HEADER]
    assert timestamp.isdigit()
    assert request.headers[SIGNATURE_HEADER] == sign(
        "s3cr3t", timestamp, request.content
    )
    body = json.loads(request.content)
    assert body["event"] == HOOK_EVENT_PRE_TOOL_USE
    assert body["tool"] == "read_pdf"
    assert body["account_id"] == "a" * 32


async def test_runner_deny_short_circuits_remaining_hooks() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers[ID_HEADER])
        return _json_response(
            {"decision": "deny", "reason": "不允许", "note": "先记一下"}
        )

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    hooks = [_hook(hook_id="hook-1"), _hook(hook_id="hook-2")]
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="write_file"
    )
    outcome = await WebhookHookRunner().run(hooks, HOOK_EVENT_PRE_TOOL_USE, payload)

    assert outcome.decision is HookDecision.DENY
    assert outcome.reason == "不允许"
    assert outcome.note == "先记一下"
    assert requests == ["hook-1"]


async def test_runner_serializes_hooks_and_joins_notes() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers[ID_HEADER])
        index = len(requests)
        return _json_response({"decision": "allow", "note": f"note-{index}"})

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    hooks = [_hook(hook_id="hook-1"), _hook(hook_id="hook-2")]
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="read_pdf"
    )
    outcome = await WebhookHookRunner().run(hooks, HOOK_EVENT_PRE_TOOL_USE, payload)

    assert requests == ["hook-1", "hook-2"]
    assert outcome.note == "note-1\nnote-2"


async def test_runner_skip_short_circuits_pre_compact() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers[ID_HEADER])
        return _json_response({"decision": "skip", "reason": "本轮不压缩"})

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    hooks = [
        _hook(hook_id="hook-1", event=HOOK_EVENT_PRE_COMPACT),
        _hook(hook_id="hook-2", event=HOOK_EVENT_PRE_COMPACT),
    ]
    payload = build_hook_payload(
        HOOK_EVENT_PRE_COMPACT, account_id="a" * 32, tokens_before=21000
    )
    outcome = await WebhookHookRunner().run(hooks, HOOK_EVENT_PRE_COMPACT, payload)

    assert outcome.decision is HookDecision.SKIP
    assert requests == ["hook-1"]


async def test_runner_ignores_hooks_for_other_events_and_disabled_rows() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _json_response({"decision": "allow"})

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    hooks = [
        _hook(event=HOOK_EVENT_POST_TOOL_USE),
        _hook(hook_id="hook-off", enabled=False),
    ]
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="read_pdf"
    )
    outcome = await WebhookHookRunner().run(hooks, HOOK_EVENT_PRE_TOOL_USE, payload)

    assert outcome.decision is HookDecision.ALLOW
    assert requests == []


async def test_runner_matcher_filters_tool_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"decision": "deny", "reason": "命中 matcher"})

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    hooks = [_hook(matcher="write_*")]
    read_payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="read_pdf"
    )
    write_payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="write_file"
    )
    assert (
        await WebhookHookRunner().run(hooks, HOOK_EVENT_PRE_TOOL_USE, read_payload)
    ).decision is HookDecision.ALLOW
    assert (
        await WebhookHookRunner().run(hooks, HOOK_EVENT_PRE_TOOL_USE, write_payload)
    ).decision is HookDecision.DENY


async def test_runner_skips_http_when_hooks_disabled() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        requests.append(request)
        return _json_response({"decision": "deny"})

    hooks_service.configure_hooks(
        _settings(hooks_enabled=False),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="read_pdf"
    )
    outcome = await WebhookHookRunner().run([_hook()], HOOK_EVENT_PRE_TOOL_USE, payload)
    assert outcome.decision is HookDecision.ALLOW
    assert requests == []


async def test_runner_failure_is_fail_open_by_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="read_pdf"
    )
    outcome = await WebhookHookRunner().run(
        [_hook(), _hook(hook_id="hook-2")], HOOK_EVENT_PRE_TOOL_USE, payload
    )
    assert outcome.decision is HookDecision.ALLOW


async def test_runner_failure_fail_closed_turns_into_deny() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True, hooks_fail_closed=True),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="read_pdf"
    )
    outcome = await WebhookHookRunner().run([_hook()], HOOK_EVENT_PRE_TOOL_USE, payload)
    assert outcome.decision is HookDecision.DENY
    assert "hooks_fail_closed" in outcome.reason


async def test_runner_rejects_oversized_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (RESPONSE_MAX_BYTES + 1))

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True),
        transport=_transport(handler),
        resolver=lambda host: [PUBLIC_ADDRESS],
    )
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="read_pdf"
    )
    outcome = await WebhookHookRunner().run([_hook()], HOOK_EVENT_PRE_TOOL_USE, payload)
    assert outcome.decision is HookDecision.ALLOW  # 超长按失败处理 → 放行


async def test_runner_blocks_private_target_without_sending_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        requests.append(request)
        return _json_response({"decision": "deny"})

    hooks_service.configure_hooks(
        _settings(hooks_enabled=True),
        transport=_transport(handler),
        resolver=lambda host: ["127.0.0.1"],
    )
    payload = build_hook_payload(
        HOOK_EVENT_PRE_TOOL_USE, account_id="a" * 32, tool="read_pdf"
    )
    outcome = await WebhookHookRunner().run(
        [_hook(target="https://internal.example/hook")],
        HOOK_EVENT_PRE_TOOL_USE,
        payload,
    )
    assert outcome.decision is HookDecision.ALLOW
    assert requests == []
