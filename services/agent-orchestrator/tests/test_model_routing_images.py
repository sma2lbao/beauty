"""§7 多模态:图片 parts、预算与带图分流(零网络)。

图片以 data URL/base64 注入 prompt,任务模式带图一律拒绝;模型清单、triage
与工具参数见 test_model_routing_models.py。
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import history, triage
from tests.conftest import _IDP, ACCOUNT, FakeLLM, _client, _settings
from tests.support.conversations import _sse_events

#: 1x1 PNG(内容本身不重要,只验证 base64 往返与 parts 形状)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24

# --------------------------------------------------------------------- §7 多模态


def _upload_image(client: TestClient, conversation_id: str, name: str) -> str:
    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": (name, PNG, "image/png")},
        headers=ACCOUNT,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_chat_prompt_carries_image_parts(tmp_path: Path) -> None:
    fake = FakeLLM(["看到了"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "看一下这张图",
                "mode": "chat",
                "attachment_ids": [image_id],
            },
            headers=ACCOUNT,
        )

    assert resp.status_code == 201
    prompt = fake.calls[0]
    content = prompt[-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "看一下这张图"}
    assert content[1]["type"] == "image_url"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG


def test_image_budget_limits_parts(tmp_path: Path) -> None:
    fake = FakeLLM(["看到了"])
    app = create_app(
        _settings(tmp_path, "img.db", llm_enabled=True, llm_image_max_count=1),
        llm_client=fake,
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        first = _upload_image(client, conversation_id, "a.png")
        second = _upload_image(client, conversation_id, "b.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "两张图",
                "mode": "chat",
                "attachment_ids": [first, second],
            },
            headers=ACCOUNT,
        )

    assert resp.status_code == 201
    content = fake.calls[0][-1]["content"]
    assert isinstance(content, list)
    assert sum(part.get("type") == "image_url" for part in content) == 1


async def test_history_prompt_inlines_images() -> None:
    rows = [
        SimpleNamespace(id="m1", role="user", content="看一下图"),
        SimpleNamespace(id="m2", role="assistant", content="好的"),
    ]
    parts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]
    prompt = history.to_prompt(rows, images_by_message={"m1": parts})

    assert prompt[0]["content"] == [{"type": "text", "text": "看一下图"}, *parts]
    assert prompt[1] == {"role": "assistant", "content": "好的"}


# ------------------------------------------------------- §7 带图分流(任务不注入图片)


def test_auto_message_with_image_goes_chat_without_triage(tmp_path: Path) -> None:
    """auto + 带图:任务路径不注入图片,所以直接直答,不调分流判别。"""
    fake = FakeLLM(["红色"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "这张图是什么颜色?", "attachment_ids": [image_id]},
            headers=ACCOUNT,
        )

    assert resp.status_code == 201
    assert resp.json()["mode"] == "chat"
    assert resp.json()["run"] is None
    # 没有分流判别调用(json_schema),第一次调用就是多模态直答
    assert triage.TRIAGE_RESPONSE_FORMAT not in fake.response_formats
    content = fake.calls[0][-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "这张图是什么颜色?"}
    assert content[1]["type"] == "image_url"


def test_task_mode_with_image_is_rejected(tmp_path: Path) -> None:
    """显式任务模式带图 → 409:任务路径看不见图,静默丢图比报错更糟。"""
    fake = FakeLLM(["不该被调用"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "整理成周报",
                "mode": "task",
                "attachment_ids": [image_id],
            },
            headers=ACCOUNT,
        )

    assert resp.status_code == 409
    assert resp.json()["code"] == "task.image_unsupported"
    assert fake.calls == []


def test_stream_task_mode_with_image_is_rejected_before_streaming(
    tmp_path: Path,
) -> None:
    """SSE 开流前就 409:一旦 200 开流,只能塞一帧错误。"""
    fake = FakeLLM(["不该被调用"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={
                "content": "整理成周报",
                "mode": "task",
                "attachment_ids": [image_id],
            },
            headers=ACCOUNT,
        )

    assert resp.status_code == 409
    assert resp.json()["code"] == "task.image_unsupported"
    assert fake.calls == []


def test_stream_auto_message_with_image_reports_chat(tmp_path: Path) -> None:
    fake = FakeLLM(["红色"])
    with _client(tmp_path, fake) as client:
        conversation_id = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]
        image_id = _upload_image(client, conversation_id, "pic.png")
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"content": "这张图是什么颜色?", "attachment_ids": [image_id]},
            headers=ACCOUNT,
        )
        events = _sse_events(resp.text)

    assert resp.status_code == 200
    meta = next(data for name, data in events if name == "meta")
    assert meta["mode"] == "chat"
    assert meta["run"] is None
    done = next(data for name, data in events if name == "done")
    assert done["reply"]["content"] == "红色"


def test_auto_message_with_image_without_llm_is_conflict(client: TestClient) -> None:
    """没有模型时带图消息 409:任务路径不注入图片,不能悄悄把图丢掉。"""
    conversation_id = client.post(
        "/api/v1/conversations", json={}, headers=ACCOUNT
    ).json()["id"]
    image_id = _upload_image(client, conversation_id, "pic.png")

    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "这张图是什么颜色?", "attachment_ids": [image_id]},
        headers=ACCOUNT,
    )

    assert resp.status_code == 409
    assert resp.json()["code"] == "chat.llm_disabled"


def test_stream_auto_message_with_image_without_llm_is_409_before_stream(
    client: TestClient,
) -> None:
    conversation_id = client.post(
        "/api/v1/conversations", json={}, headers=ACCOUNT
    ).json()["id"]
    image_id = _upload_image(client, conversation_id, "pic.png")

    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": "这张图是什么颜色?", "attachment_ids": [image_id]},
        headers=ACCOUNT,
    )

    assert resp.status_code == 409
    assert resp.json()["code"] == "chat.llm_disabled"
