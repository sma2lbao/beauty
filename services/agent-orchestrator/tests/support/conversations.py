"""conversation 域测试共享助手:建会话、发消息、取详情与 SSE 解析。"""

import json

from fastapi.testclient import TestClient

from tests.conftest import TENANT


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


def _stream_message(
    client: TestClient, conversation_id: str, content: str, *, mode: str = "auto"
):
    """走流式端点,返回 (响应, [(事件名, data)])。"""
    resp = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream",
        json={"content": content, "mode": mode},
        headers=TENANT,
    )
    return resp, _sse_events(resp.text)


def _sse_events(text: str) -> list[tuple[str, dict]]:
    """SSE 文本 → [(event 名, data)];忽略 keep-alive 注释行。"""
    events: list[tuple[str, dict]] = []
    for frame in text.split("\n\n"):
        name = data = None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if name and data:
            events.append((name, json.loads(data)))
    return events
