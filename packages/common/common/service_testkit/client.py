"""TestClient 装配:进入/退出 lifespan,支持按服务生成 conftest fixture。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@contextmanager
def app_client(app: FastAPI) -> Iterator[TestClient]:
    """进入/退出 lifespan 的 TestClient——测试必须走 lifespan 才建表/装配依赖。"""
    with TestClient(app) as test_client:
        yield test_client


def client_fixture(
    app_factory: Callable[..., FastAPI],
    settings_factory: Callable[..., Any],
    *,
    app_kwargs: Mapping[str, Any] | Callable[[], Mapping[str, Any]] = (),
    fixture_kwargs: Mapping[str, str] | None = None,
    name: str = "client",
) -> Callable[..., Iterator[TestClient]]:
    """生成 conftest 的 client fixture:tmp_path 独立库 + 进入 lifespan。

    app_kwargs: 额外传给 create_app 的固定参数(如 token_verifier);callable
    会在每个用例内调用,便于每用例现造 verifier。
    fixture_kwargs: app 参数名 -> pytest fixture 名,按需求值(如
    {"sandbox_provider": "sandbox_provider"})。
    """
    fixed_fixtures = dict(fixture_kwargs or {})

    @pytest.fixture(name=name)
    def _client(tmp_path, request) -> Iterator[TestClient]:
        kwargs = dict(app_kwargs() if callable(app_kwargs) else app_kwargs)
        kwargs.update(
            {
                app_key: request.getfixturevalue(fixture_name)
                for app_key, fixture_name in fixed_fixtures.items()
            }
        )
        app = app_factory(settings_factory(tmp_path), **kwargs)
        with app_client(app) as test_client:
            yield test_client

    return _client
