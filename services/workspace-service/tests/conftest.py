"""测试装配:每用例独立 sqlite 库 + 独立对象存储目录;鉴权开 + 真 token(§10)。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from common.testing import TestIdP
from fastapi.testclient import TestClient

from workspace.core.config import Settings
from workspace.main import create_app

_IDP = TestIdP()
TENANT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")


def verifier():
    """与 _IDP 配套的校验器——自建 app 的测试用它注入 create_app。"""
    return _IDP.verifier()


def _settings(tmp_path: Path, name: str = "test.db", **overrides) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/{name}",
        auto_create_tables=True,
        artifact_root=str(tmp_path / "objects"),
        **overrides,
    )


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(_settings(tmp_path), token_verifier=_IDP.verifier())
    with TestClient(app) as test_client:
        yield test_client


def _create_artifact(
    client: TestClient, title: str = "周报", content: str = "# 周报\n要点", **extra
):
    return client.post(
        "/v1/artifacts",
        json={"title": title, "content": content, **extra},
        headers=TENANT,
    )
