"""P0 交互闭环:消息重生成 / 编辑重发 rewrite API(设计 §2.1)。

覆盖:编辑重发替换旧回复、重生成复用前一条 user、非最后一轮 409、
活跃 run 409、content 规则 422、复用轮重新分流(chat/task)、额度闸 429、
账号隔离与目标不存在 404。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.services import quota
from tests.conftest import ACCOUNT, OTHER, FakeLLM, _client
from tests.support.conversations import (
    _create_conversation,
    _messages,
    _post_message,
    _sse_events,
)


@pytest.fixture(autouse=True)
def _reset_quota():
    """进程级配额客户端:用例结束后复位,避免漏到其他文件。"""
    yield
    quota.configure_client(base_url="", internal_secret="")


def _quota_client(payload: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    quota.configure_client(
        base_url="http://account.test",
        internal_secret="s3cret",
        transport=httpx.MockTransport(handler),
    )


def _rewrite(
    client: TestClient,
    conversation_id: str,
    message_id: str,
    payload: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] = ACCOUNT,
) -> tuple[httpx.Response, list[tuple[str, dict]]]:
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/{message_id}/rewrite",
        json={} if payload is None else payload,
        headers=headers,
    )
    return response, _sse_events(response.text)


def _attach_run(db: Path, message_id: str, run_id: str) -> None:
    """直插 DB 给消息挂一个 run:自然数据里 assistant 只挂终态 run。"""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE chat_messages SET run_id = ? WHERE id = ?",
            (run_id, message_id),
        )
        conn.commit()


def test_rewrite_edit_resends_and_replaces_reply(tmp_path) -> None:
    fake = FakeLLM(['{"mode": "chat"}', "旧回复", '{"mode": "chat"}', "新回复"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "第一版问题")
        user_id = _messages(client, conversation_id)[0]["id"]

        response, events = _rewrite(
            client, conversation_id, user_id, {"content": "改过的问题"}
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        names = [name for name, _ in events]
        # 帧序列与 /messages/stream 完全一致
        assert names[0] == "meta" and names[-1] == "done"
        assert "delta" in names

        meta = events[0][1]
        assert meta["mode"] == "chat"
        assert meta["run"] is None
        assert meta["message"]["id"] == user_id
        assert meta["message"]["role"] == "user"
        assert meta["message"]["content"] == "改过的问题"
        assert (
            "".join(payload["text"] for name, payload in events if name == "delta")
            == "新回复"
        )
        assert events[-1][1]["reply"]["content"] == "新回复"

        after = _messages(client, conversation_id)
        assert [m["role"] for m in after] == ["user", "assistant"]
        assert after[0]["id"] == user_id
        assert after[0]["content"] == "改过的问题"
        assert after[1]["content"] == "新回复"


def test_rewrite_regenerate_reuses_previous_user_turn(tmp_path) -> None:
    fake = FakeLLM(['{"mode": "chat"}', "第一版", '{"mode": "chat"}', "第二版"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "同一个问题")
        before = _messages(client, conversation_id)
        user_id, assistant_id = before[0]["id"], before[1]["id"]

        response, events = _rewrite(client, conversation_id, assistant_id)

        assert response.status_code == 200
        names = [name for name, _ in events]
        assert names[0] == "meta" and names[-1] == "done"
        assert events[0][1]["message"]["id"] == user_id
        assert events[0][1]["message"]["content"] == "同一个问题"
        assert events[-1][1]["reply"]["content"] == "第二版"
        # 复用轮:模型提示词末尾仍是原 user 正文,没有另落一条 user 消息
        assert fake.calls[-1][-1]["content"] == "同一个问题"

        after = _messages(client, conversation_id)
        assert [m["role"] for m in after] == ["user", "assistant"]
        assert after[0]["id"] == user_id
        assert after[1]["id"] != assistant_id
        assert after[1]["content"] == "第二版"


def test_rewrite_edit_keeps_attachment_binding(tmp_path) -> None:
    fake = FakeLLM(["旧回复", '{"mode": "chat"}', "新回复"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        uploaded = client.post(
            f"/api/v1/conversations/{conversation_id}/attachments",
            files={"file": ("note.md", b"# hello", "text/markdown")},
            headers=ACCOUNT,
        )
        assert uploaded.status_code == 201
        attachment_id = uploaded.json()["id"]
        posted = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "看下附件",
                "mode": "chat",
                "attachment_ids": [attachment_id],
            },
            headers=ACCOUNT,
        )
        assert posted.status_code == 201
        user_id = posted.json()["message"]["id"]

        response, _ = _rewrite(
            client, conversation_id, user_id, {"content": "再看看附件"}
        )

        assert response.status_code == 200
        listing = client.get(
            f"/api/v1/conversations/{conversation_id}/attachments",
            headers=ACCOUNT,
        ).json()
        assert listing[0]["id"] == attachment_id
        # 编辑只改正文,附件仍挂在这条 user 消息上
        assert listing[0]["message_id"] == user_id


def test_rewrite_requires_latest_turn(tmp_path) -> None:
    fake = FakeLLM(
        [
            '{"mode": "chat"}',
            "一",
            '{"mode": "chat"}',
            "二",
            '{"mode": "chat"}',
            "新的二",
        ]
    )
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "第一问")
        _post_message(client, conversation_id, "第二问")
        rows = _messages(client, conversation_id)
        assert len(rows) == 4

        stale_user, _ = _rewrite(
            client, conversation_id, rows[0]["id"], {"content": "改第一问"}
        )
        assert stale_user.status_code == 409
        assert stale_user.json()["code"] == "conversation.message_not_latest"

        stale_assistant, _ = _rewrite(client, conversation_id, rows[1]["id"])
        assert stale_assistant.status_code == 409
        assert stale_assistant.json()["code"] == "conversation.message_not_latest"

        # 最后一轮仍可重写
        latest_user, _ = _rewrite(
            client, conversation_id, rows[2]["id"], {"content": "改第二问"}
        )
        assert latest_user.status_code == 200
        after = _messages(client, conversation_id)
        assert [m["content"] for m in after][:3] == ["第一问", "一", "改第二问"]


def test_rewrite_rejects_active_run(tmp_path) -> None:
    fake = FakeLLM(['{"mode": "chat"}', "旧回复", "# 执行计划\n- [ ] 做"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "帮我做点事")
        rows = _messages(client, conversation_id)
        created = client.post(
            "/api/v1/runs",
            json={"title": "任务", "goal": "做点事", "acceptance_criteria": ["完成"]},
            headers=ACCOUNT,
        )
        assert created.status_code == 201
        _attach_run(tmp_path / "llm.db", rows[1]["id"], created.json()["id"])

        edit, _ = _rewrite(
            client, conversation_id, rows[0]["id"], {"content": "改问题"}
        )
        assert edit.status_code == 409
        assert edit.json()["code"] == "conversation.run_active"

        regenerate, _ = _rewrite(client, conversation_id, rows[1]["id"])
        assert regenerate.status_code == 409
        assert regenerate.json()["code"] == "conversation.run_active"

        # 校验失败不改数据:旧轮次原样保留
        assert [m["id"] for m in _messages(client, conversation_id)] == [
            row["id"] for row in rows
        ]


def test_rewrite_content_rules(tmp_path) -> None:
    fake = FakeLLM(['{"mode": "chat"}', "回复"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "问题")
        rows = _messages(client, conversation_id)

        missing, _ = _rewrite(client, conversation_id, rows[0]["id"], {})
        assert missing.status_code == 422
        assert missing.json()["code"] == "conversation.content_required"

        empty, _ = _rewrite(client, conversation_id, rows[0]["id"], {"content": ""})
        assert empty.status_code == 422
        assert empty.json()["code"] == "conversation.content_required"

        extra, _ = _rewrite(
            client, conversation_id, rows[1]["id"], {"content": "改回复"}
        )
        assert extra.status_code == 422
        assert extra.json()["code"] == "conversation.content_not_allowed"

        assert len(_messages(client, conversation_id)) == 2


def test_rewrite_retriages_and_can_start_run(tmp_path) -> None:
    fake = FakeLLM(
        [
            '{"mode": "chat"}',
            "旧回复",
            '{"mode": "task"}',
            "# 执行计划\n- [ ] 做",
        ]
    )
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "闲聊一下")
        user_id = _messages(client, conversation_id)[0]["id"]

        response, events = _rewrite(
            client, conversation_id, user_id, {"content": "帮我做个任务"}
        )

        assert response.status_code == 200
        assert [name for name, _ in events] == ["meta", "done"]
        meta = events[0][1]
        # 复用轮重新分流:同一账号同一会话可以变成任务
        assert meta["mode"] == "run_created"
        assert meta["run"]["status"] == "awaiting_approval"
        assert events[-1][1]["reply"] is None
        assert len(fake.calls) == 4

        rows = _messages(client, conversation_id)
        assert [m["role"] for m in rows] == ["user"]
        assert rows[0]["content"] == "帮我做个任务"


def test_rewrite_enforces_monthly_quota(tmp_path) -> None:
    fake = FakeLLM(['{"mode": "chat"}', "回复"])
    with _client(tmp_path, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "问题")
        rows = _messages(client, conversation_id)
        _quota_client(
            {
                "monthly_tokens": 1000,
                "max_concurrent_runs": None,
                "used_tokens": 1000,
            }
        )

        response, _ = _rewrite(client, conversation_id, rows[1]["id"])

        assert response.status_code == 429
        body = response.json()
        assert body["code"] == "quota.monthly_tokens_exceeded"
        assert body["details"] == {"limit": 1000, "used": 1000}
        # 开流前拦截:目标消息没被删
        assert [m["id"] for m in _messages(client, conversation_id)] == [
            row["id"] for row in rows
        ]


def test_rewrite_unknown_targets_are_404(client: TestClient) -> None:
    conversation_id = _create_conversation(client)

    missing_conversation, _ = _rewrite(client, "nope", "whatever")
    assert missing_conversation.status_code == 404
    assert missing_conversation.json()["code"] == "conversation.not_found"

    missing_message, _ = _rewrite(client, conversation_id, "nope")
    assert missing_message.status_code == 404
    assert missing_message.json()["code"] == "conversation.message_not_found"

    # 账号隔离:别人的会话一律 404,不泄漏存在性
    foreign, _ = _rewrite(client, conversation_id, "nope", headers=OTHER)
    assert foreign.status_code == 404
    assert foreign.json()["code"] == "conversation.not_found"
