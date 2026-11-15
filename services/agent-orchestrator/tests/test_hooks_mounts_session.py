"""P1-B 会话事件挂点:SessionStart/SessionEnd 在提交后触发,hook 失败不拖垮会话。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from tests.conftest import ACCOUNT
from tests.support.hooks_mounts import (
    _create_conversation,
    _hooks_client,
    _HookServer,
    _install_hook,
)

# ---------------------------------------------------------------- 会话事件


def test_session_start_and_end_fire_after_commit(tmp_path: Path) -> None:
    server = _HookServer(lambda body, index: {"decision": "allow"})
    with _hooks_client(tmp_path, server=server, replies=[]) as client:
        _install_hook(client, "SessionStart")
        _install_hook(client, "SessionEnd")
        conversation_id = _create_conversation(client)
        delete = client.delete(
            f"/api/v1/conversations/{conversation_id}", headers=ACCOUNT
        )

    assert delete.status_code == 204
    assert [body["event"] for body in server.bodies] == ["SessionStart", "SessionEnd"]
    assert server.bodies[0]["conversation_id"] == conversation_id
    assert server.bodies[1]["conversation_id"] == conversation_id


def test_session_hook_failure_does_not_break_conversation(tmp_path: Path) -> None:
    def responder(body: dict[str, Any], index: int) -> Any:
        return httpx.ConnectError("boom")

    server = _HookServer(responder)
    with _hooks_client(tmp_path, server=server, replies=[]) as client:
        _install_hook(client, "SessionStart")
        conversation_id = _create_conversation(client)

    assert conversation_id
    assert len(server.bodies) == 1
