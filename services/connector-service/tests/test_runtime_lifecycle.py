"""后台作业容器与 lifespan:开关关闭零任务、开启能启停、transport 随关闭释放。"""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from connector.main import create_app
from tests.conftest import _IDP, _settings


def test_runtime_is_empty_when_switches_off(client: TestClient) -> None:
    """测试/单进程默认:不起任何后台任务,也不建总线。"""
    runtime = client.app.state.connector_runtime
    assert runtime is not None
    assert runtime.task_count == 0
    assert runtime.dispatcher is None
    assert runtime.bus is None


def test_runtime_starts_and_stops_background_tasks(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        outbox_enabled=True,
        refresh_patrol_enabled=True,
        refresh_patrol_interval_seconds=1,
    )
    app = create_app(settings, token_verifier=_IDP.verifier())

    with TestClient(app) as test_client:
        runtime = test_client.app.state.connector_runtime
        assert runtime.task_count == 2
        assert runtime.dispatcher is not None
        assert runtime.bus is not None
        assert test_client.get("/healthz").status_code == 200

    # 退出 lifespan:停止信号已发、任务已清空(不泄漏)
    assert runtime.stopped is True
    assert runtime.task_count == 0


def test_lifespan_closes_injected_transport(tmp_path: Path) -> None:
    closed: list[bool] = []

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(
            self, request: httpx.Request
        ) -> httpx.Response:  # pragma: no cover - 测试不发请求
            raise AssertionError("unexpected request")

        async def aclose(self) -> None:
            closed.append(True)

    app = create_app(
        _settings(tmp_path),
        token_verifier=_IDP.verifier(),
        http_transport=_Transport(),
    )
    with TestClient(app):
        pass

    assert closed == [True]
