"""账号级 run 状态流(SSE):复合游标、摘要帧、账号隔离与端点契约。

约定:临时 sqlite 文件库 + 真实 Run 行;生成器直测(端点只做参数解析与响应包装),
避免对长连接做"读到末尾"的阻塞断言。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from common.errors import AppError
from database.base import Base
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories.run import RunRepository
from agent_orchestrator.services.runs_stream import (
    RUN_STATE_EVENT,
    RUNS_STREAM_LOOKBACK,
    encode_runs_cursor,
    parse_runs_cursor,
    runs_stream,
)
from tests.conftest import ACCOUNT, _settings

ACCOUNT_ID = "t-1"
OTHER_ID = "t-2"


@pytest.fixture
async def factory(
    tmp_path,
) -> async_sessionmaker[AsyncSession]:
    """独立 sqlite 文件库:生成器直测不经过 HTTP,自己建表。"""
    settings = _settings(tmp_path, "runs-stream.db")
    session_factory = create_session_factory(settings.database_url)
    async with session_factory.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield session_factory
    await session_factory.engine.dispose()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return _settings(tmp_path, "runs-stream-settings.db")


async def _seed(factory, account_id: str, title: str) -> str:
    """直接落一条 run 行(不跑图):状态流只读 runs 表。"""
    async with session_scope(factory) as session:
        run = Run(account_id=account_id, title=title, goal=title, status="running")
        session.add(run)
        await session.flush()
        return run.id


async def _take(
    factory,
    settings: Settings,
    account_id: str,
    *,
    cursor: tuple[datetime, str] | None = None,
    states: int = 1,
) -> list[str]:
    """取到 N 个状态帧就停(账号级流没有终态,不能读到结束)。"""
    frames: list[str] = []
    stream = runs_stream(factory, settings, account_id, cursor)
    try:
        async for frame in stream:
            frames.append(frame)
            if (
                sum(1 for item in frames if f"event: {RUN_STATE_EVENT}" in item)
                >= states
            ):
                break
    finally:
        await stream.aclose()
    return frames


def _frames(frames: list[str]) -> list[tuple[str | None, str, dict | None]]:
    parsed: list[tuple[str | None, str, dict | None]] = []
    for block in frames:
        if block.startswith(":"):
            # 注释帧(`: ready` / `: keep-alive`)不是事件,跳过
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
        parsed.append((sid, event, json.loads(data) if data else None))
    return parsed


async def test_frames_carry_full_summary_and_cursor(
    factory, settings: Settings
) -> None:
    run_id = await _seed(factory, ACCOUNT_ID, "推我")
    frames = _frames(await _take(factory, settings, ACCOUNT_ID))
    assert frames[0][1] == RUN_STATE_EVENT
    sid, _, data = frames[0]
    payload = data or {}
    assert payload["cursor"] == sid
    run = payload["run"]
    # 摘要与 GET /runs 的列表项同形:前端可直接 upsert 进 ['runs'] 缓存
    assert run["id"] == run_id
    assert run["title"] == "推我"
    assert run["status"] == "running"
    assert {"goal", "created_at"} <= set(run)
    assert payload["cursor"].endswith(f"|{run_id}")


async def test_cursor_only_replays_newer_changes(factory, settings: Settings) -> None:
    await _seed(factory, ACCOUNT_ID, "第一条")
    first = _frames(await _take(factory, settings, ACCOUNT_ID))
    cursor = first[0][0]
    assert cursor is not None
    await _seed(factory, ACCOUNT_ID, "第二条")
    frames = _frames(
        await _take(factory, settings, ACCOUNT_ID, cursor=parse_runs_cursor(cursor))
    )
    ids = [payload["run"]["title"] for _, _, payload in frames if payload]
    # 游标之后只推新行:第一条不会重放
    assert ids == ["第二条"]


async def test_account_isolation(factory, settings: Settings) -> None:
    await _seed(factory, ACCOUNT_ID, "我的")
    await _seed(factory, OTHER_ID, "别人的")
    frames = _frames(await _take(factory, settings, ACCOUNT_ID))
    titles = [payload["run"]["title"] for _, _, payload in frames if payload]
    assert titles == ["我的"]
    # 仓库层同样按账号收口:另一个账号从同一游标查不到我的行
    async with session_scope(factory) as session:
        rows = await RunRepository(session).list_changed_after(
            OTHER_ID, datetime.now(UTC) - RUNS_STREAM_LOOKBACK, ""
        )
    assert [row.title for row in rows] == ["别人的"]


async def _seed_many(factory, count: int) -> list[str]:
    return [await _seed(factory, ACCOUNT_ID, f"行-{index}") for index in range(count)]


async def test_batch_limit_is_paged_by_cursor(factory, settings: Settings) -> None:
    """一批拉满立刻接着读下一页:游标逐行推进,不重复也不漏。"""
    await _seed_many(factory, 3)
    frames = _frames(await _take(factory, settings, ACCOUNT_ID, states=3))
    ids = [payload["run"]["id"] for _, _, payload in frames if payload]
    assert len(ids) == len(set(ids)) == 3


def test_parse_cursor_contract() -> None:
    assert parse_runs_cursor(None) is None
    assert parse_runs_cursor("") is None
    run = type(
        "_Run",
        (),
        {"id": "r1", "updated_at": datetime(2026, 11, 18, 12, 0, tzinfo=UTC)},
    )()
    cursor = encode_runs_cursor(run)
    parsed = parse_runs_cursor(cursor)
    assert parsed is not None
    assert parsed[1] == "r1"
    assert parsed[0] == datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    for bad in ("nope", "2026-11-18T12:00:00", "|r1", "2026-11-18T12:00:00|"):
        with pytest.raises(AppError) as excinfo:
            parse_runs_cursor(bad)
        assert excinfo.value.code == "sse.bad_cursor"


def test_stream_route_is_registered_before_run_id(client: TestClient) -> None:
    """路由顺序契约:`/stream` 必须先于 `/{run_id}` 注册,否则被当成 run_id → 404。

    本版 FastAPI 把 include_router 收成 `_IncludedRouter`,app.routes 上看不到扁平 path,
    改从 OpenAPI 的 paths 顺序断言(它按注册顺序产出)。
    """
    paths = list(client.app.openapi()["paths"])
    assert paths.index("/api/v1/runs/stream") < paths.index("/api/v1/runs/{run_id}")


def test_endpoint_bad_cursor_is_400(client: TestClient) -> None:
    response = client.get(
        "/api/v1/runs/stream", headers={**ACCOUNT, "Last-Event-ID": "nope"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "sse.bad_cursor"


def test_endpoint_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/runs/stream").status_code == 401
