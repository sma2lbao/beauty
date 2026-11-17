"""执行期预留与在飞取消(S6)。

test_api_tools_execute 用同步 TestClient,只能验证"执行结束之后"的幂等;
这里要的是"执行正在进行"的现场,所以用 httpx.AsyncClient + ASGITransport
并在同一个事件循环里手动跑 lifespan,才能并发发起第二个请求、或在中途取消。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from database.session import session_scope
from sqlalchemy import select

from tests.conftest import _IDP, ACCOUNT, FakeSandboxProvider, _settings
from tool_service.main import create_app
from tool_service.models.tool_call import (
    STATUS_CANCELLED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    ToolCall,
)
from tool_service.services.execution import arguments_digest

KEY = "orch1:" + "b" * 64


class BlockingSandboxProvider(FakeSandboxProvider):
    """ensure 卡在一个事件上:制造"预留已提交、执行还没结束"的窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def ensure(
        self, account_id: str, *, scope_key: str, run_id: str | None = None
    ) -> str:
        self.entered.set()
        await self.release.wait()
        return await super().ensure(account_id, scope_key=scope_key, run_id=run_id)


@asynccontextmanager
async def _app(
    tmp_path, provider: FakeSandboxProvider | None = None, **overrides: Any
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    settings = _settings(tmp_path, **overrides)
    app = create_app(
        settings, token_verifier=_IDP.verifier(), sandbox_provider=provider
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://tool.test"
        ) as client:
            yield app, client


def _body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool_call_id": "call-1",
        "idempotency_key": KEY,
        "arguments": {"path": "a.txt"},
        "run_id": "run-1",
    }
    payload.update(overrides)
    return payload


async def _insert_running(
    app: Any,
    *,
    lease_until: datetime | None = None,
    arguments: dict[str, Any] | None = None,
) -> None:
    """直接写一条 running 行,模拟"另一个进程正在执行"或"进程崩了留下租约"。"""

    arguments = arguments if arguments is not None else {"path": "a.txt"}
    async with session_scope(app.state.session_factory) as session:
        session.add(
            ToolCall(
                account_id="t-1",
                tool_call_id="call-1",
                idempotency_key=KEY,
                run_id="run-1",
                tool_name="read",
                tool_version="1",
                risk="read",
                decision="auto",
                arguments="{}",
                arguments_sha256=arguments_digest(arguments),
                status=STATUS_RUNNING,
                lease_until=lease_until or datetime.now(UTC) - timedelta(seconds=5),
                cancel_requested=False,
            )
        )


async def _get_row(app: Any) -> ToolCall:
    async with session_scope(app.state.session_factory) as session:
        row = await session.scalar(
            select(ToolCall).where(ToolCall.tool_call_id == "call-1")
        )
    assert row is not None
    return row


async def test_in_flight_same_key_returns_conflict(tmp_path) -> None:
    """同键第二次请求在执行途中就 409,而不是执行完再撞唯一约束。"""

    provider = BlockingSandboxProvider()
    async with _app(tmp_path, provider) as (app, client):
        first = asyncio.create_task(
            client.post("/v1/tools/read:execute", json=_body(), headers=ACCOUNT)
        )
        await asyncio.wait_for(provider.entered.wait(), timeout=5)

        second = await client.post(
            "/v1/tools/read:execute", json=_body(), headers=ACCOUNT
        )
        assert second.status_code == 409
        payload = second.json()
        assert payload["code"] == "tool.call_in_progress"
        assert payload["details"]["tool_call_id"] == "call-1"
        assert payload["details"]["idempotency_key"] == KEY
        # 第一次调用还挂着,只有它真正执行一次。
        assert len(provider.exec_calls) == 0

        provider.release.set()
        response = await first
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert len(provider.exec_calls) == 1
        row = await _get_row(app)
        assert row.status == STATUS_SUCCEEDED
        assert row.lease_until is None


async def test_expired_lease_is_taken_over(tmp_path) -> None:
    """进程崩在 running 后,租约过期即可被同键请求抢占重跑。"""

    provider = FakeSandboxProvider()
    async with _app(tmp_path, provider) as (app, client):
        await _insert_running(app, lease_until=datetime.now(UTC) - timedelta(seconds=5))
        response = await client.post(
            "/v1/tools/read:execute", json=_body(), headers=ACCOUNT
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert len(provider.exec_calls) == 1
        row = await _get_row(app)
        assert row.status == STATUS_SUCCEEDED


async def test_live_lease_is_not_stolen(tmp_path) -> None:
    """租约未过期说明另一次调用仍在跑:409,不能抢占。"""

    provider = FakeSandboxProvider()
    async with _app(tmp_path, provider) as (app, client):
        await _insert_running(
            app, lease_until=datetime.now(UTC) + timedelta(seconds=600)
        )
        response = await client.post(
            "/v1/tools/read:execute", json=_body(), headers=ACCOUNT
        )
        assert response.status_code == 409
        assert response.json()["code"] == "tool.call_in_progress"
        assert response.json()["details"]["lease_until"] is not None
        assert provider.exec_calls == []


async def test_running_row_with_other_arguments_conflicts(tmp_path) -> None:
    """同键异参优先按幂等冲突报 409,和已结束的记录口径一致。"""

    provider = FakeSandboxProvider()
    async with _app(tmp_path, provider) as (app, client):
        await _insert_running(
            app,
            lease_until=datetime.now(UTC) - timedelta(seconds=5),
            arguments={"path": "other.txt"},
        )
        response = await client.post(
            "/v1/tools/read:execute", json=_body(), headers=ACCOUNT
        )
        assert response.status_code == 409
        assert response.json()["code"] == "tool.idempotency_conflict"
        assert provider.exec_calls == []


async def test_cancel_endpoint_marks_running_rows(tmp_path) -> None:
    provider = FakeSandboxProvider()
    async with _app(tmp_path, provider) as (app, client):
        await _insert_running(
            app, lease_until=datetime.now(UTC) + timedelta(seconds=600)
        )
        response = await client.post(
            "/v1/tool-calls:cancel", json={"run_id": "run-1"}, headers=ACCOUNT
        )
        assert response.status_code == 200
        assert response.json() == {"cancelled": 1}
        assert (await _get_row(app)).cancel_requested is True

        again = await client.post(
            "/v1/tool-calls:cancel", json={"run_id": "run-1"}, headers=ACCOUNT
        )
        assert again.json() == {"cancelled": 1}  # 仍是 running 行,重复置位幂等

        missing = await client.post("/v1/tool-calls:cancel", json={}, headers=ACCOUNT)
        assert missing.status_code == 422


async def test_cancel_stops_waiting_execution(tmp_path) -> None:
    """取消后执行方放弃等待:回工具错误信封(200 + ok=false),行落 cancelled。"""

    provider = BlockingSandboxProvider()
    async with _app(tmp_path, provider) as (app, client):
        pending = asyncio.create_task(
            client.post("/v1/tools/read:execute", json=_body(), headers=ACCOUNT)
        )
        await asyncio.wait_for(provider.entered.wait(), timeout=5)

        cancel = await client.post(
            "/v1/tool-calls:cancel", json={"run_id": "run-1"}, headers=ACCOUNT
        )
        assert cancel.json() == {"cancelled": 1}

        response = await asyncio.wait_for(pending, timeout=5)
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is False
        assert payload["error"]["code"] == "tool.cancelled"
        assert payload["error"]["retryable"] is True
        row = await _get_row(app)
        assert row.status == STATUS_CANCELLED
        assert row.ok is False
        assert row.lease_until is None
        provider.release.set()


async def test_cancelled_call_replays_as_cancelled(tmp_path) -> None:
    """已取消的行再按同键请求:仍回取消错误,而不是"执行失败"。"""

    provider = FakeSandboxProvider()
    async with _app(tmp_path, provider) as (app, client):
        await _insert_running(app, lease_until=datetime.now(UTC) - timedelta(seconds=5))
        async with session_scope(app.state.session_factory) as session:
            row = await session.scalar(
                select(ToolCall).where(ToolCall.tool_call_id == "call-1")
            )
            assert row is not None
            row.status = STATUS_CANCELLED
            row.ok = False
            row.error_code = "tool.cancelled"
        response = await client.post(
            "/v1/tools/read:execute", json=_body(), headers=ACCOUNT
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is False
        assert payload["idempotent_replay"] is True
        assert payload["error"]["code"] == "tool.cancelled"


async def test_replay_returns_stored_result(tmp_path) -> None:
    """第二次同键请求回第一次的真实结果(不只是"已执行"提示)。"""

    provider = FakeSandboxProvider()
    async with _app(tmp_path, provider) as (app, client):
        first = await client.post(
            "/v1/tools/read:execute", json=_body(), headers=ACCOUNT
        )
        assert first.json()["ok"] is True
        assert "content" in first.json()

        second = await client.post(
            "/v1/tools/read:execute", json=_body(), headers=ACCOUNT
        )
        payload = second.json()
        assert payload["ok"] is True
        assert payload["idempotent_replay"] is True
        assert payload["replayed"] is True
        assert payload["content"] == first.json()["content"]
        assert payload["note"].startswith("该逻辑调用")
        assert len(provider.exec_calls) == 1
