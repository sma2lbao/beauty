"""测试装配:每用例独立 sqlite 库;本地嵌入提供方(零网络);鉴权开 + 真 token。

库/客户端样板来自 common.service_testkit(见 AGENTS.md「测试编排」)。
"""

from __future__ import annotations

from common.service_testkit import client_fixture, settings_factory
from common.testing import TestIdP
from fastapi.testclient import TestClient

from knowledge.core.config import Settings
from knowledge.main import create_app

_IDP = TestIdP()
TENANT = _IDP.bearer("t-1")
OTHER = _IDP.bearer("t-2")


def verifier():
    """与 _IDP 配套的校验器——自建 app 的测试用它注入 create_app。"""
    return _IDP.verifier()


_settings = settings_factory(Settings)
client = client_fixture(
    create_app,
    _settings,
    app_kwargs=lambda: {"token_verifier": _IDP.verifier()},
)


def _ingest(client: TestClient, title: str, content: str, tenant: dict | None = None):
    return client.post(
        "/v1/knowledge/documents",
        json={"title": title, "content": content},
        headers=tenant or TENANT,
    )
