"""会话级工具调用审计端点:跨 run 聚合、会话归属校验与 tool-service 降级。

补丁由 tool-service 在落库时生成,这里只做转发与校验;tool-service 不可达时
必须 200 + degraded=true(前端回退本地重建),不能炸整条请求。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.errors import UpstreamError
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import _IDP, OTHER, TENANT, _settings


def _audit_item(**overrides: Any) -> dict[str, Any]:
    """一条 tool-service 审计行(字段与 ToolCallRead 对齐)。"""

    item: dict[str, Any] = {
        "id": "tc-1",
        "tool_call_id": "call-1",
        "run_id": "run-1",
        "conversation_id": "c-1",
        "tool_name": "write",
        "tool_version": "1",
        "risk": "write",
        "decision": "auto",
        "ok": True,
        "error_code": None,
        "duration_ms": 12,
        "result_chars": 40,
        "bytes_written": 12,
        "truncated": False,
        "sha256_before": None,
        "sha256_after": "a" * 64,
        "paths": ["a.txt"],
        "patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n",
        "lines_added": 1,
        "lines_removed": 1,
        "patch_truncated": False,
        "sandbox_id": "sbx-1",
        "created_at": "2026-01-01T00:00:00Z",
    }
    item.update(overrides)
    return item


class FakeToolClient:
    """tool-service 审计查询替身:记录入参,可注入不可达。"""

    def __init__(
        self, payload: dict[str, Any] | None = None, *, fail: bool = False
    ) -> None:
        self.payload = payload or {"items": [], "total": 0, "limit": 50, "offset": 0}
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def list_tool_calls(
        self,
        tenant_id: str,
        *,
        conversation_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "limit": limit,
                "offset": offset,
            }
        )
        if self.fail:
            raise UpstreamError(
                "tool_service.unreachable", "tool-service 不可达: ConnectError"
            )
        return self.payload


def _app(tmp_path: Path, name: str, tool_client: Any = None) -> Any:
    """显式钉住 tool_service_enabled=False(.env 会被 Nx 注入 true)。"""

    settings = _settings(tmp_path, name, tool_service_enabled=False)
    return create_app(settings, token_verifier=_IDP.verifier(), tool_client=tool_client)


def _create_conversation(client: TestClient) -> str:
    response = client.post("/api/v1/conversations", json={}, headers=TENANT)
    assert response.status_code == 201
    return response.json()["id"]


def _tool_calls(client: TestClient, conversation_id: str, query: str = "") -> Any:
    return client.get(
        f"/api/v1/conversations/{conversation_id}/tool-calls{query}", headers=TENANT
    )


def test_returns_server_generated_patch_across_runs(tmp_path: Path) -> None:
    fake = FakeToolClient(
        {"items": [_audit_item()], "total": 1, "limit": 50, "offset": 0}
    )
    with TestClient(_app(tmp_path, "tool-calls.db", fake)) as client:
        conversation_id = _create_conversation(client)
        response = _tool_calls(client, conversation_id)

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is False
    assert body["total"] == 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    item = body["items"][0]
    # 补丁原样透出:前端不再从工具参数(受 2000 字符截断)重建
    assert item["patch"].startswith("--- a/a.txt")
    assert item["lines_added"] == 1
    assert item["lines_removed"] == 1
    assert item["patch_truncated"] is False
    assert item["run_id"] == "run-1"
    assert fake.calls == [
        {
            "tenant_id": "t-1",
            "conversation_id": conversation_id,
            "limit": 50,
            "offset": 0,
        }
    ]


def test_passes_pagination_through(tmp_path: Path) -> None:
    fake = FakeToolClient({"items": [], "total": 0, "limit": 10, "offset": 20})
    with TestClient(_app(tmp_path, "tool-calls-page.db", fake)) as client:
        conversation_id = _create_conversation(client)
        response = _tool_calls(client, conversation_id, "?limit=10&offset=20")

    assert response.status_code == 200
    assert response.json()["limit"] == 10
    assert response.json()["offset"] == 20
    assert fake.calls[0]["limit"] == 10
    assert fake.calls[0]["offset"] == 20


def test_rejects_invalid_pagination(tmp_path: Path) -> None:
    fake = FakeToolClient()
    with TestClient(_app(tmp_path, "tool-calls-bad-page.db", fake)) as client:
        conversation_id = _create_conversation(client)
        assert _tool_calls(client, conversation_id, "?limit=0").status_code == 422
        assert _tool_calls(client, conversation_id, "?offset=-1").status_code == 422
    assert fake.calls == []


def test_cross_tenant_conversation_is_404(tmp_path: Path) -> None:
    fake = FakeToolClient()
    with TestClient(_app(tmp_path, "tool-calls-other.db", fake)) as client:
        conversation_id = _create_conversation(client)
        response = client.get(
            f"/api/v1/conversations/{conversation_id}/tool-calls", headers=OTHER
        )

    assert response.status_code == 404
    assert response.json()["code"] == "conversation.not_found"
    assert fake.calls == []  # 归属校验先于上游查询


def test_missing_conversation_is_404(tmp_path: Path) -> None:
    fake = FakeToolClient()
    with TestClient(_app(tmp_path, "tool-calls-missing.db", fake)) as client:
        response = _tool_calls(client, "no-such-conversation")

    assert response.status_code == 404
    assert response.json()["code"] == "conversation.not_found"
    assert fake.calls == []


def test_degrades_when_tool_service_unreachable(tmp_path: Path) -> None:
    fake = FakeToolClient(fail=True)
    with TestClient(_app(tmp_path, "tool-calls-degraded.db", fake)) as client:
        conversation_id = _create_conversation(client)
        response = _tool_calls(client, conversation_id)

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["items"] == []
    assert body["total"] == 0
    assert fake.calls[0]["conversation_id"] == conversation_id


def test_degrades_without_tool_client(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, "tool-calls-disabled.db")) as client:
        conversation_id = _create_conversation(client)
        response = _tool_calls(client, conversation_id)

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["items"] == []
    assert body["total"] == 0


def test_degrades_when_audit_payload_is_invalid(tmp_path: Path) -> None:
    fake = FakeToolClient({"items": [{"id": "tc-1"}]})
    with TestClient(_app(tmp_path, "tool-calls-invalid.db", fake)) as client:
        conversation_id = _create_conversation(client)
        response = _tool_calls(client, conversation_id)

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["items"] == []
