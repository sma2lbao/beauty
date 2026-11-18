"""关闭兜底(AppContainer.stop,2026-11-18):挂住的任务/资源不能拖住停服。

背景:门禁跑全量时偶发卡在 `with TestClient(app)` 退场(星形 testclient.py
wait_shutdown),现象是全进程只剩几条空闲线程、关闭在等一个永不完成的 await。
stop() 原来对每个后台任务/资源都是无超时的 `await`,一个不响应取消的等待就能
让进程退出无限挂住。本文件钉住三条行为:

1. 挂住的后台任务:stop() 在 stop_timeout_seconds 后放手并记 warning;
2. 挂住的 closer:同上,且**其余 closer 仍会被调用**(关闭不能一个坏资源全停);
3. 正常任务/资源:照常关闭,不留超时噪音(用 0.5s 超时验证正常路径不误报)。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import _IDP, FakeLLM, _settings

TIMEOUT = 0.05


def _app(tmp_path: Path, name: str, **overrides: object):
    values: dict[str, object] = {"stop_timeout_seconds": TIMEOUT, **overrides}
    return create_app(
        _settings(tmp_path, name, **values),
        llm_client=FakeLLM(["好"]),
        token_verifier=_IDP.verifier(),
    )


def test_stop_gives_up_on_stuck_task(tmp_path: Path) -> None:
    """后台任务吞掉取消也不拖住 stop():等超时就放手,退出仍是秒级。"""
    app = _app(tmp_path, "stuck-task.db")
    client = TestClient(app)
    with client:
        container = client.app.state.container

        async def forever() -> None:
            await asyncio.Event().wait()

        async def make() -> asyncio.Task[None]:
            return asyncio.create_task(forever(), name="stuck-sweeper")

        container.notification_sweep_task = client.portal.call(make)
        started = time.monotonic()
    assert time.monotonic() - started < 2.0


def test_stop_gives_up_on_stuck_closer_and_closes_the_rest(tmp_path: Path) -> None:
    """closer 挂住不影响其余 closer:记录方仍在 stop 期间被调到。"""
    app = _app(tmp_path, "stuck-closer.db")
    client = TestClient(app)
    calls: list[str] = []
    with client:
        container = client.app.state.container

        async def stuck() -> None:
            await asyncio.Event().wait()

        async def normal() -> None:
            calls.append("normal")

        container._closers.append(("normal", normal))
        container._closers.append(("stuck", stuck))
        started = time.monotonic()
    assert time.monotonic() - started < 2.0
    assert calls == ["normal"]


def test_stop_closes_quickly_when_nothing_is_stuck(tmp_path: Path) -> None:
    """正常路径:两个周期任务 + 资源都很快关掉,不应触发任何超时兜底。"""
    app = _app(tmp_path, "fast-stop.db", stop_timeout_seconds=0.5)
    client = TestClient(app)
    clock: list[float] = []

    async def tick() -> None:
        while True:
            clock.append(time.monotonic())
            await asyncio.sleep(0.01)

    with client:
        container = client.app.state.container

        async def make() -> asyncio.Task[None]:
            return asyncio.create_task(tick(), name="ticker")

        container.notification_sweep_task = client.portal.call(make)
        started = time.monotonic()
    elapsed = time.monotonic() - started
    assert clock  # 任务确实在跑
    assert elapsed < 0.5
