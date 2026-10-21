"""通知流(P1-9b):diff 纯函数 + SSE 端点契约。"""

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_orchestrator.api.v1.notifications import (
    diff_notifications,
    notification_for,
    notification_stream,
)

#: conftest 里 TENANT = _IDP.bearer("t-1")
TENANT_ID = "t-1"


def _run(run_id: str, status: str, conversation_id: str | None = None):
    return SimpleNamespace(
        id=run_id,
        status=status,
        title="周报",
        goal="把日程整理成周报",
        conversation_id=conversation_id,
        updated_at=None,
    )


def test_notification_for_只对需要提醒的状态产出载荷() -> None:
    assert notification_for(_run("r1", "running")) is None
    payload = notification_for(_run("r1", "completed", "c1"))
    assert payload is not None
    assert payload["status"] == "completed"
    assert payload["conversation_id"] == "c1"
    assert payload["title"] == "周报"


def test_diff_首次只建基线_之后只推状态变化() -> None:
    baseline, pushed = diff_notifications({}, [_run("r1", "running")])
    assert pushed == []
    assert baseline == {"r1": "running"}

    # 中间态变化不推,但快照要更新
    snapshot, pushed = diff_notifications(baseline, [_run("r1", "pending")])
    assert pushed == []
    assert snapshot == {"r1": "pending"}

    # 终态变化 → 推
    snapshot, pushed = diff_notifications(snapshot, [_run("r1", "completed", "c1")])
    assert len(pushed) == 1
    assert pushed[0]["status"] == "completed"

    # 同一状态重复扫描不推(防抖)
    _, pushed = diff_notifications(snapshot, [_run("r1", "completed", "c1")])
    assert pushed == []


def test_stream_首帧就绪(client: TestClient) -> None:
    """无限 SSE 不能用 TestClient 流式消费(会挂住),直接驱动生成器取首帧。"""
    factory = client.app.state.session_factory
    settings = client.app.state.settings

    async def first_frame() -> str:
        gen = notification_stream(factory, settings, TENANT_ID)
        try:
            return await gen.__anext__()
        finally:
            await gen.aclose()

    assert asyncio.run(first_frame()) == ": ready\n\n"


def test_stream_未带租户令牌被拒(client: TestClient) -> None:
    resp = client.get("/api/v1/notifications/stream")
    assert resp.status_code == 401


def test_notification_stream_推送状态变化(client: TestClient) -> None:
    """同一事件循环内:建基线 → 状态变化 → 下一轮扫描推出通知帧。

    注意:不能跨 TestClient(它跑在 app 自己的循环/线程里,session 缓存会让
    新循环读到陈旧状态),所以这个用例直接走 app 的 session_factory。
    """
    from database.session import session_scope
    from sqlalchemy import select

    from agent_orchestrator.models.runs import Run

    factory = client.app.state.session_factory
    settings = client.app.state.settings
    settings.notifications_poll_interval_seconds = 0.01

    async def scenario() -> tuple[list[str], str]:
        async with session_scope(factory) as session:
            run = Run(
                tenant_id=TENANT_ID,
                title="推送",
                goal="看通知",
                status="pending",
            )
            session.add(run)
            await session.flush()
            run_id = run.id

        gen = notification_stream(factory, settings, TENANT_ID)
        frames: list[str] = []
        try:
            frames.append(await gen.__anext__())  # : ready —— 基线已建立
            async with session_scope(factory) as session:
                row = await session.scalar(select(Run).where(Run.id == run_id))
                row.status = "completed"
            for _ in range(60):
                chunk = await gen.__anext__()
                frames.append(chunk)
                if "run.status_changed.v1" in chunk:
                    break
        finally:
            await gen.aclose()
        return frames, run_id

    frames, run_id = asyncio.run(scenario())
    joined = "".join(frames)
    assert "run.status_changed.v1" in joined
    payload = json.loads(
        [line[6:] for line in joined.splitlines() if line.startswith("data: ")][0]
    )
    assert payload["run_id"] == run_id
    assert payload["status"] == "completed"
