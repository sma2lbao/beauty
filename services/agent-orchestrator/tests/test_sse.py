"""SSE 事件流:重放、Last-Event-ID 续传、终态关闭;非终态生成器单测直测。"""

import json

from database.base import Base
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.api.v1.runs import _event_stream
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.runs import RunCreate
from agent_orchestrator.services import runs as runs_service
from tests.conftest import TENANT


def _completed_run(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/runs", json={"title": "流式", "goal": "看事件流"}, headers=TENANT
    )
    run_id = resp.json()["id"]
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    return run_id


def _read_stream(client: TestClient, run_id: str, extra: dict | None = None) -> str:
    with client.stream(
        "GET",
        f"/api/v1/runs/{run_id}/events/stream",
        headers={**TENANT, **(extra or {})},
    ) as response:
        assert response.status_code == 200
        return b"".join(response.iter_raw()).decode()


def _parse_sse(text: str) -> list[tuple[str | None, str, dict | None]]:
    events = []
    for block in text.split("\n\n"):
        if not block or block.startswith(":"):
            continue
        sid = data = None
        event = ""
        for line in block.splitlines():
            if line.startswith("id: "):
                sid = line[4:]
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        events.append((sid, event, json.loads(data) if data else None))
    return events


def test_stream_replays_and_closes_on_terminal(client: TestClient) -> None:
    run_id = _completed_run(client)
    events = _parse_sse(_read_stream(client, run_id))
    assert [e[1] for e in events] == [
        "run.created.v1",
        "run.planned.v1",
        "run.approved.v1",
        "run.delivered.v1",
        "run.completed.v1",
        "stream.end",
    ]
    # id 即 seq 游标:因为 run.step.v1(S1 步骤记录)也占用 seq 但不出现在
    # 用户事件流里,可见事件的 id 只保证严格递增,不一定连续。
    ids = [int(e[0] or 0) for e in events[:5]]
    assert ids[0] == 1
    assert ids == sorted(ids)
    assert len(set(ids)) == 5
    assert events[-1][2] == {"status": "completed"}


def test_stream_resumes_from_last_event_id(client: TestClient) -> None:
    run_id = _completed_run(client)
    replay = _parse_sse(_read_stream(client, run_id))
    cursor = replay[1][0]  # planned 事件(第 2 条可见事件)的 seq
    assert cursor is not None
    events = _parse_sse(_read_stream(client, run_id, {"Last-Event-ID": cursor}))
    assert [e[1] for e in events] == [
        "run.approved.v1",
        "run.delivered.v1",
        "run.completed.v1",
        "stream.end",
    ]
    # 续传游标语义:只回放 seq > cursor 的事件(step 记录占号,故 id 不连续)
    ids = [int(e[0] or 0) for e in events[:3]]
    assert all(sid > int(cursor) for sid in ids)
    assert ids == sorted(ids)


def test_stream_unknown_run_404(client: TestClient) -> None:
    resp = client.get("/api/v1/runs/nope/events/stream", headers=TENANT)
    assert resp.status_code == 404
    assert resp.json()["code"] == "run.not_found"


def test_stream_bad_cursor_rejected(client: TestClient) -> None:
    run_id = _completed_run(client)
    resp = client.get(
        f"/api/v1/runs/{run_id}/events/stream",
        headers={**TENANT, "Last-Event-ID": "abc"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "sse.bad_cursor"


async def test_event_stream_emits_keepalive_comment_frame(tmp_path) -> None:
    """空轮询按 sse_keepalive_polls 周期发注释帧,客户端解析时跳过(契约 3)。"""
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/k.db")
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_scope(factory) as session:
        run = await runs_service.create_run(
            session, "t-1", RunCreate(title="心跳", goal="看注释帧")
        )

    settings = Settings(
        _env_file=None,
        env="dev",
        sse_poll_interval_seconds=0.01,
        sse_keepalive_polls=1,
    )
    stream = _event_stream(factory, settings, "t-1", run.id, after=0)
    assert "run.created.v1" in await anext(stream)
    assert await anext(stream) == ": keep-alive\n\n"
    await stream.aclose()
    await factory.engine.dispose()


async def test_event_stream_stays_open_until_terminal(tmp_path) -> None:
    """非终态 run:生成器重放完不结束,可随时干净关闭(断线重连的根基)。

    直测异步生成器而不经 TestClient——TestClient 关闭不结束的 SSE 流会死锁。
    """
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_scope(factory) as session:
        run = await runs_service.create_run(
            session, "t-1", RunCreate(title="流式", goal="看事件流")
        )
        await runs_service.append_event(session, run, "run.planned.v1", {})

    settings = Settings(_env_file=None, env="dev", sse_poll_interval_seconds=0.01)
    stream = _event_stream(factory, settings, "t-1", run.id, after=0)
    assert "run.created.v1" in await anext(stream)
    assert "run.planned.v1" in await anext(stream)
    # run 处于 pending(非终态),生成器持续轮询不终止;直接关闭验证可取消
    await stream.aclose()
    await factory.engine.dispose()
